"""
Live Replay Gauntlet — replay real historical candles through the ACTUAL live
execution functions (check_cascade_add_trigger, place_entry, check_pending_entry,
check_pending_add, check_all) tick-by-tick against a local Postgres sandbox
(same schema tests/live/ uses), and compare the result to the backtest
engine's own numbers for the identical window and config. This is the real
proof that the live port matches the backtest — not just that each function
is individually plausible in a unit test, and not just that the backtest is
internally consistent (the Gauntlet already covers that).

Formalized version of tools/live_replay_wip/live_replay_gauntlet.py. Two
changes from that draft:

1. TICK ORDER NOW MATCHES PRODUCTION EXACTLY. blended_executor.py's real
   tick() does, per tick: (A) cascade-add trigger check, (B) entry signal
   check/placement, (C) poll pending-entry fills, (D) poll pending-add fills,
   (E) stop/trailing/capitulation check (check_all) — in that order. Notably
   A and B run BEFORE C/D, so an order placed this tick can be filled and
   then evaluated by E within the SAME tick. The original draft ran the fill
   polls (C, D) before placing a new cascade-add (A), which delayed a
   same-tick fill from ever being seen by the stop-check until the NEXT
   tick. That's the bug this rebuild fixes — see HANDOFF.md's "Critical Open
   Item" for the Trade 2 discrepancy this was built to resolve.
2. Parametrized on stream_config_id/version/date range instead of hardcoded
   to GS: Reflex — every stream, present and future, should be able to run
   this before it's trusted live, not just Model 4's.

SAFETY: both real notifier modules are mocked for the ENTIRE script run --
not env vars (proven fragile: a later load_dotenv() call silently refills a
deleted var — see HANDOFF.md, two real alerts fired before this pattern was
adopted) but a direct patch of the one function every alert funnels through
in each module, verified via an assert BEFORE anything else runs. Do not
simplify this away.

Usage:
    python -m tools.live_replay.replay_gauntlet --stream-config-id 12 --version v2 \\
        --start 2026-01-01 --end 2026-01-31 --lot-size 20 --slot-count 5 --slot-mode blended
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
from sqlalchemy import text

from src.app.db import load_stream_configs, get_local_engine
from src.backtester.engine import load_market_data, _warmup_days, run_backtest
from src.backtester.indicators import add_indicators, resample_ohlcv
from src.backtester.signals import generate_signals
from src.backtester.metrics import compute_metrics
from src.data.sentiment import load_sentiment
import src.live.notifier as notifier
import src.live.blended_notifier as blended_notifier
from src.live import blended_order_manager as order_manager
from src.live import blended_position_monitor as position_monitor

TEST_MODEL_VERSION = 991  # reserved sentinel, never a real deployed model


def _verify_alerts_mocked():
    print("Notifier mock check (must print True for both before anything else runs):", flush=True)
    with mock.patch("src.live.blended_notifier._dispatch") as m1:
        blended_notifier.alert_blend_opened("safety-check", 0, 1.0, 1.0, 1.0)
        print("  blended_notifier._dispatch intercepted:", m1.called, flush=True)
        assert m1.called
    with mock.patch("src.live.notifier._dispatch") as m2:
        notifier.alert_system_down(0.0)
        print("  notifier._dispatch intercepted:", m2.called, flush=True)
        assert m2.called
    print("Safety check passed -- proceeding with real (mocked-alert) replay.\n", flush=True)


class ReplayKraken:
    """Fake Kraken client: limit orders fill only if the candle's range touches
    the limit price (real touch-fill semantics, not the backtest's perfect-fill
    assumption); market orders fill at the candle's close (real slippage)."""

    def __init__(self):
        self.orders = {}
        self._next_id = 1
        self.current_candle = None

    def get_ticker_price(self):
        return self.current_candle["close"]

    def place_order(self, side, volume_btc, price_usd=None, order_type="limit"):
        txid = f"R{self._next_id}"
        self._next_id += 1
        if order_type == "market":
            price = self.current_candle["close"]
            fee = volume_btc * price * 0.008
            self.orders[txid] = {"status": "closed", "vol_exec": f"{volume_btc:.8f}",
                                  "price": f"{price:.2f}", "fee": f"{fee:.4f}"}
        else:
            self.orders[txid] = {"status": "open", "vol_exec": "0.00000000", "price": "0.00",
                                  "fee": "0.0000", "_limit_price": price_usd, "_volume": volume_btc}
        return txid

    def get_order_status(self, txid):
        order = self.orders[txid]
        if order["status"] == "open" and "_limit_price" in order:
            lp = order["_limit_price"]
            c = self.current_candle
            if c["low"] <= lp <= c["high"]:
                fee = order["_volume"] * lp * 0.004
                order.update({"status": "closed", "vol_exec": f"{order['_volume']:.8f}",
                              "price": f"{lp:.2f}", "fee": f"{fee:.4f}"})
        return {k: v for k, v in order.items() if not k.startswith("_")}

    def cancel_order(self, txid):
        order = self.orders[txid]
        if order["status"] == "open":
            order["status"] = "canceled"


def _cleanup(conn, version):
    conn.execute(text("""
        DELETE FROM live.blended_fills WHERE position_id IN
        (SELECT position_id FROM live.blended_positions WHERE model_id IN
         (SELECT model_id FROM live.models WHERE model_version = :v))
    """), {"v": version})
    conn.execute(text("DELETE FROM live.blended_positions WHERE model_id IN "
                       "(SELECT model_id FROM live.models WHERE model_version = :v)"), {"v": version})
    conn.execute(text("DELETE FROM live.blended_capital WHERE model_id IN "
                       "(SELECT model_id FROM live.models WHERE model_version = :v)"), {"v": version})
    conn.execute(text("DELETE FROM live.streams WHERE model_id IN "
                       "(SELECT model_id FROM live.models WHERE model_version = :v)"), {"v": version})
    conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": version})


class _FakeDT(datetime):
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


def run_replay(stream_config_id: int, version: str, start: str, end: str,
               lot_size_usd: float = 20.0, slot_count: int = None, slot_mode: str = None,
               stream_name: str = None):
    _verify_alerts_mocked()

    cfg = next(c for c in load_stream_configs(stream_config_id) if c["version"] == version)
    params = cfg["params"]
    slot_count = slot_count or cfg["slot_count"]
    slot_mode = slot_mode or cfg["slot_mode"]
    stream_name = stream_name or cfg.get("stream_name", f"config-{stream_config_id}-{version}")
    tf = params.get("primary_timeframe")
    total_capital = lot_size_usd * slot_count

    warmup = _warmup_days(params)
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup)).strftime("%Y-%m-%d")

    df = load_market_data(load_start, end)
    df = resample_ohlcv(df, tf)
    fng_map = load_sentiment(load_start, end)
    df["fng_value"] = df.index.date
    df["fng_value"] = df["fng_value"].map(fng_map)
    df = add_indicators(df, params)
    df = df[df.index >= pd.Timestamp(start)]
    signals = generate_signals(df, params)

    print(f"Replaying {len(df)} candles from {df.index[0]} to {df.index[-1]} "
          f"({stream_name} {version}, ${lot_size_usd}x{slot_count} slots)", flush=True)

    # NOTE: run_backtest's `lot_size_usd` kwarg is actually TOTAL capital for
    # blended mode (see _run_blended_slots's `total_capital` param) despite the
    # name -- pass the stream's total pool, not the per-slot amount.
    bt_result = run_backtest(params=params, start=start, end=end,
                              slot_count=slot_count, slot_mode=slot_mode,
                              stream_name=stream_name, lot_size_usd=total_capital)
    bt_metrics = compute_metrics(bt_result["trades"], total_capital, bt_result["start"], bt_result["end"])
    print(f"BACKTEST reference: trades={bt_metrics['total_trades']} total_pnl={bt_metrics['total_pnl']}", flush=True)
    if len(bt_result["trades"]):
        print(bt_result["trades"][["entry_ts", "exit_ts", "entry_price", "exit_price",
                                    "pnl", "exit_reason"]].to_string(), flush=True)

    engine = get_local_engine()
    with engine.begin() as conn:
        _cleanup(conn, TEST_MODEL_VERSION)
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'REPLAY-TEST -- not a real model, alerts mocked', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'REPLAY-TEST', :ver, 'blended_dca', CAST(:p AS jsonb), :slots, :mode, :lot)
            RETURNING stream_id
        """), {"mid": model_id, "ver": version, "p": json.dumps(params),
               "slots": slot_count, "mode": slot_mode, "lot": lot_size_usd}).scalar()
        conn.execute(text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, :cap)"),
                     {"mid": model_id, "cap": total_capital})

    stream = {"stream_id": stream_id, "model_id": model_id, "stream_name": "REPLAY-TEST",
              "parameters": params, "slot_count": slot_count, "slot_mode": slot_mode,
              "lot_size_usd": lot_size_usd}
    streams = {stream_id: stream}
    kraken = ReplayKraken()

    try:
        with mock.patch("src.live.blended_notifier._dispatch"), \
             mock.patch("src.live.notifier._dispatch"), \
             mock.patch("src.live.blended_order_manager.datetime", _FakeDT):
            for i, (ts, row) in enumerate(df.iterrows()):
                _FakeDT._now = ts.to_pydatetime().replace(tzinfo=timezone.utc)
                kraken.current_candle = {"open": row["open"], "high": row["high"],
                                          "low": row["low"], "close": row["close"]}
                candle_row = {stream_id: {"close": row["close"], "low": row["low"]}}

                with engine.begin() as conn:
                    # (A) cascade-add trigger — matches blended_executor.tick()'s
                    # real order: placed BEFORE this tick's fill polling, so a
                    # fill from an order placed this tick can be seen by (E)
                    # this same tick, exactly like production.
                    if order_manager.has_active_position(conn, stream_id):
                        order_manager.check_cascade_add_trigger(conn, stream, row["close"], kraken, dry_run=False)

                    # (B) fresh entry signal — only if no active position
                    if not order_manager.has_active_position(conn, stream_id) and bool(signals.loc[ts]):
                        order_manager.place_entry(conn, stream, kraken, dry_run=False)

                    # (C), (D) poll fills — may include orders placed above, same tick.
                    # Also polls any resting exit limit order from a PRIOR tick's
                    # arming (see (E) below) -- matches blended_executor.tick()'s order.
                    order_manager.check_pending_entry(conn, kraken, streams, dry_run=False)
                    order_manager.check_pending_add(conn, kraken, streams, dry_run=False)
                    order_manager.check_pending_exit(conn, kraken, streams, dry_run=False)

                    # (E) stop / trailing / capitulation check, this tick's candle --
                    # places/re-prices a resting exit limit order once armed (see
                    # ensure_pending_exit), rather than an immediate market sell.
                    position_monitor.check_all(conn, streams, candle_row, {tf}, kraken, dry_run=False)

                if i % 20 == 0:
                    print(f"  ...tick {i}/{len(df)} ({ts})", flush=True)

        with engine.begin() as conn:
            closed = conn.execute(text("""
                SELECT p.position_id, p.original_entry_price, p.avg_cost_basis, p.exit_price, p.realized_pnl,
                       p.exit_reason, p.opened_at, p.closed_at,
                       (SELECT COUNT(*) FROM live.blended_fills f WHERE f.position_id = p.position_id) AS num_slots
                FROM live.blended_positions p WHERE p.model_id = :mid AND p.status = 'CLOSED' ORDER BY p.opened_at
            """), {"mid": model_id}).fetchall()
            open_pos = conn.execute(text("""
                SELECT position_id, status FROM live.blended_positions
                WHERE model_id = :mid AND status IN ('OPEN','PENDING_ENTRY')
            """), {"mid": model_id}).fetchall()

        print(f"\nLIVE REPLAY: {len(closed)} closed, {len(open_pos)} still open/pending", flush=True)
        for c in closed:
            print(f"  slots={c.num_slots} entry_avg={c.avg_cost_basis} exit={c.exit_price} pnl={c.realized_pnl} "
                  f"reason={c.exit_reason} opened={c.opened_at} closed={c.closed_at}", flush=True)
        for o in open_pos:
            print(f"  still {o.status}: position_id={o.position_id}", flush=True)

        return {"backtest": bt_result, "backtest_metrics": bt_metrics,
                "live_closed": closed, "live_open": open_pos}
    finally:
        with engine.begin() as conn:
            _cleanup(conn, TEST_MODEL_VERSION)
        print("\nCleanup done.", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stream-config-id", type=int, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--lot-size", type=float, default=20.0)
    p.add_argument("--slot-count", type=int, default=None)
    p.add_argument("--slot-mode", default=None)
    p.add_argument("--stream-name", default=None)
    args = p.parse_args()

    run_replay(args.stream_config_id, args.version, args.start, args.end,
               lot_size_usd=args.lot_size, slot_count=args.slot_count,
               slot_mode=args.slot_mode, stream_name=args.stream_name)


if __name__ == "__main__":
    main()
