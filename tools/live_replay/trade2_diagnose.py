"""
Narrowly re-run just Jan 19-30 2026 with full per-tick instrumentation of the
OPEN position's stop level, to find out exactly why the live replay's exit
fired at $82,650 instead of ~$90,307 like the backtest. Alerts mocked and
verified BEFORE any real order-placement logic runs, same as before.

NOTE: this script still uses the OLD (pre-fix) tick ordering -- it predates
replay_gauntlet.py's rebuild to match blended_executor.tick()'s real order
(cascade-add/entry check before fill polling). Kept for reference on how the
original root-cause trace was done; re-run replay_gauntlet.py first if you
need current numbers -- confirmed the discrepancy is real, not a harness
ordering artifact (see HANDOFF.md).
"""
import sys, json, os
sys.path.insert(0, "/Users/reverierevival/Documents/forge-anchor-collective")

from datetime import datetime, timezone
from unittest import mock

import pandas as pd
from sqlalchemy import text

from src.app.db import load_stream_configs, get_local_engine
from src.backtester.engine import load_market_data, _warmup_days
from src.backtester.indicators import add_indicators, resample_ohlcv
from src.backtester.signals import generate_signals
from src.data.sentiment import load_sentiment
import src.live.notifier as notifier
import src.live.blended_notifier as blended_notifier
from src.live import blended_order_manager as order_manager
from src.live import blended_position_monitor as position_monitor
from src.live.order_manager import TAKER_FEE

TEST_MODEL_VERSION = 993
START, END = "2026-01-01", "2026-01-31"

print("Safety check:", flush=True)
with mock.patch("src.live.blended_notifier._dispatch") as m1:
    blended_notifier.alert_blend_opened("safety-check", 0, 1.0, 1.0, 1.0)
    assert m1.called
with mock.patch("src.live.notifier._dispatch") as m2:
    notifier.alert_system_down(0.0)
    assert m2.called
print("  both mocks intercepted correctly -- proceeding.\n", flush=True)

cfg = next(c for c in load_stream_configs(12) if c["version"] == "v2")
params = cfg["params"]
position_params = params["position"]
tf = params.get("primary_timeframe")
warmup = _warmup_days(params)
load_start = (pd.Timestamp(START) - pd.Timedelta(days=warmup)).strftime("%Y-%m-%d")

df = load_market_data(load_start, END)
df = resample_ohlcv(df, tf)
fng_map = load_sentiment(load_start, END)
df["fng_value"] = df.index.date
df["fng_value"] = df["fng_value"].map(fng_map)
df = add_indicators(df, params)
df = df[df.index >= pd.Timestamp(START)]
signals = generate_signals(df, params)


class ReplayKraken:
    def __init__(self):
        self.orders = {}
        self._next_id = 1
        self.current_candle = None

    def get_ticker_price(self):
        return self.current_candle["close"]

    def place_order(self, side, volume_btc, price_usd=None, order_type="limit"):
        txid = f"R{self._next_id}"; self._next_id += 1
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


engine = get_local_engine()


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


with engine.begin() as conn:
    _cleanup(conn, TEST_MODEL_VERSION)
    model_id = conn.execute(text("""
        INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
        VALUES (:v, 'DIAGNOSE-TEST -- not real, alerts mocked', 0, 'active') RETURNING model_id
    """), {"v": TEST_MODEL_VERSION}).scalar()
    stream_id = conn.execute(text("""
        INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                  parameters, slot_count, slot_mode, lot_size_usd)
        VALUES (:mid, 'DIAGNOSE-TEST', 'v2', 'blended_dca', CAST(:p AS jsonb), 5, 'blended', 20.0)
        RETURNING stream_id
    """), {"mid": model_id, "p": json.dumps(params)}).scalar()
    conn.execute(text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, 100.00)"),
                 {"mid": model_id})

stream = {"stream_id": stream_id, "model_id": model_id, "stream_name": "DIAGNOSE-TEST",
          "parameters": params, "slot_count": cfg["slot_count"], "slot_mode": cfg["slot_mode"],
          "lot_size_usd": 100.0}
streams = {stream_id: stream}
kraken = ReplayKraken()


class _FakeDT(datetime):
    _now = None
    @classmethod
    def now(cls, tz=None):
        return cls._now


try:
    with mock.patch("src.live.blended_notifier._dispatch"), \
         mock.patch("src.live.notifier._dispatch"), \
         mock.patch("src.live.blended_order_manager.datetime", _FakeDT):
        for i, (ts, row) in enumerate(df.iterrows()):
            _FakeDT._now = ts.to_pydatetime().replace(tzinfo=timezone.utc)
            kraken.current_candle = {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}

            with engine.begin() as conn:
                order_manager.check_pending_entry(conn, kraken, streams, dry_run=False)
                order_manager.check_pending_add(conn, kraken, streams, dry_run=False)

                if order_manager.has_active_position(conn, stream_id):
                    order_manager.check_cascade_add_trigger(conn, stream, row["close"], kraken, dry_run=False)

                candle_row = {stream_id: {"close": row["close"], "low": row["low"]}}

                # --- instrumentation: print the stop calc BEFORE calling check_all,
                # only while a position from the 2nd trade window is open ---
                if pd.Timestamp("2026-01-19") <= ts <= pd.Timestamp("2026-01-31"):
                    pos = conn.execute(text("""
                        SELECT position_id, status, avg_cost_basis, highest_close, total_qty,
                               original_entry_price, capitulation_armed
                        FROM live.blended_positions WHERE stream_id = :sid AND status = 'OPEN'
                    """), {"sid": stream_id}).fetchone()
                    if pos:
                        fill_count = conn.execute(text(
                            "SELECT COUNT(*) FROM live.blended_fills WHERE position_id = :pid"
                        ), {"pid": pos.position_id}).scalar()
                        avg_ep = float(pos.avg_cost_basis)
                        new_hwm = max(float(pos.highest_close), row["close"])
                        gain_pct = (new_hwm - avg_ep) / avg_ep * 100
                        trail_arm = position_params.get("trail_arm_gain_pct")
                        armed = (not trail_arm) or gain_pct >= trail_arm
                        trail_pct = position_params["trailing_stop_pct"]
                        stop_price = None
                        if trail_pct and armed:
                            stop_price = new_hwm * (1 - trail_pct / 100.0)
                            breakeven = avg_ep / (1 - TAKER_FEE)
                            margin = position_params.get("shallow_breakeven_margin_pct")
                            thresh = position_params.get("shallow_slot_threshold", 3)
                            if margin and fill_count <= thresh:
                                breakeven *= (1 + margin / 100.0)
                            stop_price = max(stop_price, breakeven)
                        would_exit = stop_price is not None and row["low"] <= stop_price
                        print(f"{ts} fills={fill_count} avg_ep={avg_ep:.2f} hwm={new_hwm:.2f} "
                              f"armed={armed} stop={stop_price} low={row['low']:.2f} high={row['high']:.2f} "
                              f"WOULD_EXIT={would_exit}", flush=True)

                position_monitor.check_all(conn, streams, candle_row, {tf}, kraken, dry_run=False)

                if not order_manager.has_active_position(conn, stream_id) and bool(signals.loc[ts]):
                    order_manager.place_entry(conn, stream, kraken, dry_run=False)

    with engine.begin() as conn:
        closed = conn.execute(text("""
            SELECT position_id, original_entry_price, avg_cost_basis, exit_price, realized_pnl,
                   exit_reason, opened_at, closed_at
            FROM live.blended_positions WHERE model_id = :mid AND status = 'CLOSED' ORDER BY opened_at
        """), {"mid": model_id}).fetchall()
    print(f"\nFinal closed trades: {len(closed)}", flush=True)
    for c in closed:
        print(f"  {c.opened_at} -> {c.closed_at}: entry={c.original_entry_price} avg={c.avg_cost_basis} "
              f"exit={c.exit_price} pnl={c.realized_pnl} reason={c.exit_reason}", flush=True)

finally:
    with engine.begin() as conn:
        _cleanup(conn, TEST_MODEL_VERSION)
    print("\nCleanup done.", flush=True)
