"""
Small Model 1 (plain, single-slot) live-replay check — parallel to
tools/live_replay/replay_gauntlet.py (which only covers blended mode).

Model 1 stays on a real MARKET sell for its trailing stop, by deliberate
choice (a stop-loss should guarantee execution, not rest unfilled during a
crash) -- so this isn't testing the same "unreachable fill price" bug
replay_gauntlet.py found. It's here to see, on real data, whether Model 1's
live order_manager.py/position_monitor.py reproduce the (now also fixed,
"no fill better than candle close") backtest at all, the same class of
check this project now does for every model before trusting it.

SAFETY: identical pattern to replay_gauntlet.py -- both real notifier
modules mocked for the ENTIRE script run, verified via an assert BEFORE
anything else runs. Do not simplify this away.

Usage:
    python -m tools.live_replay.replay_model1 --stream-id 1 --version v2 \\
        --start 2022-01-01 --end 2022-03-01 --lot-size 33.33
"""
import argparse
import json
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
from src.live import order_manager
from src.live import position_monitor
from tools.live_replay.replay_gauntlet import ReplayKraken, _verify_alerts_mocked

TEST_MODEL_VERSION = 992  # reserved sentinel, never a real deployed model


def _cleanup(conn, version):
    conn.execute(text("""
        DELETE FROM live.lots WHERE model_id IN
        (SELECT model_id FROM live.models WHERE model_version = :v)
    """), {"v": version})
    conn.execute(text("DELETE FROM live.streams WHERE model_id IN "
                       "(SELECT model_id FROM live.models WHERE model_version = :v)"), {"v": version})
    conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": version})


class _FakeDT(datetime):
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


def run_replay(stream_id: int, version: str, start: str, end: str, lot_size_usd: float, stream_name: str = None):
    _verify_alerts_mocked()

    cfg = next(c for c in load_stream_configs(stream_id) if c["version"] == version)
    params = cfg["params"]
    stream_name = stream_name or cfg.get("stream_name", f"config-{stream_id}-{version}")
    tf = params.get("primary_timeframe")

    warmup = _warmup_days(params)
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup)).strftime("%Y-%m-%d")

    df = load_market_data(load_start, end)
    df = resample_ohlcv(df, tf)
    if params.get("sentiment"):
        fng_map = load_sentiment(load_start, end)
        df["fng_value"] = df.index.date
        df["fng_value"] = df["fng_value"].map(fng_map)
    df = add_indicators(df, params)
    df = df[df.index >= pd.Timestamp(start)]
    signals = generate_signals(df, params)

    print(f"Replaying {len(df)} candles from {df.index[0]} to {df.index[-1]} "
          f"({stream_name} {version}, ${lot_size_usd} single-slot)", flush=True)

    bt_result = run_backtest(params=params, start=start, end=end, slot_count=1,
                              slot_mode="single", stream_name=stream_name, lot_size_usd=lot_size_usd)
    bt_metrics = compute_metrics(bt_result["trades"], lot_size_usd, bt_result["start"], bt_result["end"])
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
        db_stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'REPLAY-TEST', :ver, 'single', CAST(:p AS jsonb), 1, 'single', :lot)
            RETURNING stream_id
        """), {"mid": model_id, "ver": version, "p": json.dumps(params), "lot": lot_size_usd}).scalar()

    stream = {"stream_id": db_stream_id, "model_id": model_id, "stream_name": "REPLAY-TEST",
              "parameters": params, "slot_count": 1, "slot_mode": "single", "lot_size_usd": lot_size_usd}
    streams = {db_stream_id: stream}
    kraken = ReplayKraken()

    try:
        with mock.patch("src.live.blended_notifier._dispatch"), \
             mock.patch("src.live.notifier._dispatch"), \
             mock.patch("src.live.order_manager.datetime", _FakeDT):
            for i, (ts, row) in enumerate(df.iterrows()):
                _FakeDT._now = ts.to_pydatetime().replace(tzinfo=timezone.utc)
                kraken.current_candle = {"open": row["open"], "high": row["high"],
                                          "low": row["low"], "close": row["close"]}
                candle_row = {db_stream_id: {"close": row["close"], "low": row["low"]}}

                with engine.begin() as conn:
                    if order_manager.slot_is_available(conn, db_stream_id, slot_number=1) and bool(signals.loc[ts]):
                        order_manager.place_entry(conn, stream, kraken, dry_run=False)

                    order_manager.check_pending(conn, kraken, dry_run=False)

                    position_monitor.check_all(conn, streams, candle_row, {tf}, kraken, dry_run=False)

                if i % 100 == 0:
                    print(f"  ...tick {i}/{len(df)} ({ts})", flush=True)

        with engine.begin() as conn:
            closed = conn.execute(text("""
                SELECT lot_id, entry_price, exit_price, realized_pnl, exit_reason, opened_at, closed_at
                FROM live.lots WHERE model_id = :mid AND status = 'CLOSED' ORDER BY opened_at
            """), {"mid": model_id}).fetchall()
            open_lots = conn.execute(text("""
                SELECT lot_id, status FROM live.lots
                WHERE model_id = :mid AND status IN ('OPEN', 'PENDING')
            """), {"mid": model_id}).fetchall()

        print(f"\nLIVE REPLAY: {len(closed)} closed, {len(open_lots)} still open/pending", flush=True)
        for c in closed:
            print(f"  entry={c.entry_price} exit={c.exit_price} pnl={c.realized_pnl} "
                  f"reason={c.exit_reason} opened={c.opened_at} closed={c.closed_at}", flush=True)
        for o in open_lots:
            print(f"  still {o.status}: lot_id={o.lot_id}", flush=True)
    finally:
        with engine.begin() as conn:
            _cleanup(conn, TEST_MODEL_VERSION)
        print("\nCleanup done.", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stream-id", type=int, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--lot-size", type=float, default=33.33)
    p.add_argument("--stream-name", default=None)
    args = p.parse_args()

    run_replay(args.stream_id, args.version, args.start, args.end,
               lot_size_usd=args.lot_size, stream_name=args.stream_name)


if __name__ == "__main__":
    main()
