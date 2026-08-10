"""
Combined multi-stream live-replay for ANY model's real locked composition
(all its streams in ONE sandbox model, ticked together) -- generalizes
replay_model2.py (which hardcoded Model 2's 4 streams) to load composition
from backtest.model_streams for any --model-version, so the same tool
verifies Model 1, Model 2, or any future model without editing code.

Streams are evaluated only on their OWN candle-close boundary (mixed
timeframes handled correctly), same as production's executor.tick() /
_detect_closed_timeframes.

No compounding, no pooled reserve, no BTC bucket -- this replays exactly
what the model would do if deployed today: order_manager.place_entry (opt-in
reserve check finds no live.capital_reserve row for this sandbox model, so
sizes at each stream's plain configured lot_size_usd) and
position_monitor.check_all (max_hold/min_hold/stop_loss_pct all enforced,
per the 2026-08-07 fixes). Single-slot streams only -- raises otherwise.

SAFETY: identical pattern to replay_model1.py/replay_gauntlet.py -- both
real notifier modules mocked for the ENTIRE script run, verified via an
assert BEFORE anything else runs.

Usage:
    python -m tools.live_replay.replay_model --model-version 1 --start 2022-01-01 --end 2026-08-09
    python -m tools.live_replay.replay_model --model-version 2 --start 2022-01-01 --end 2026-08-09
"""
import argparse
import json
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
from sqlalchemy import text

from src.app.db import get_local_engine
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

TEST_MODEL_VERSION = 993  # reserved sentinel for this script -- distinct from 991 (blended) / 992 (replay_model1)


def _load_locked_streams(model_version: int) -> list:
    engine = get_local_engine()
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT sc.stream_id, sc.version, s.stream_name || ' ' || sc.version AS full_name,
                   ms.lot_size_usd, sc.slot_count, sc.slot_mode, sc.parameters
            FROM backtest.model_streams ms
            JOIN backtest.models m ON ms.model_id = m.model_id
            JOIN backtest.stream_configs sc ON ms.stream_config_id = sc.stream_config_id
            JOIN backtest.streams s ON sc.stream_id = s.stream_id
            WHERE m.model_version = :v
            ORDER BY s.stream_name
        """), {"v": model_version}).fetchall()
    if not rows:
        raise ValueError(f"No locked composition found in backtest.model_streams for model_version={model_version}")
    streams = []
    for r in rows:
        if r.slot_count != 1 or r.slot_mode != "single":
            raise ValueError(f"replay_model.py only supports single-slot streams "
                             f"(got {r.full_name}: slot_count={r.slot_count}, slot_mode={r.slot_mode})")
        params = r.parameters if isinstance(r.parameters, dict) else json.loads(r.parameters)
        streams.append({"stream_id": r.stream_id, "version": r.version, "label": r.full_name,
                        "lot_size": float(r.lot_size_usd), "params": params})
    return streams


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


def run_replay(model_version: int, start: str, end: str):
    _verify_alerts_mocked()

    model_streams = _load_locked_streams(model_version)

    engine = get_local_engine()
    with engine.begin() as conn:
        _cleanup(conn, TEST_MODEL_VERSION)
        sandbox_model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, :desc, 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION, "desc": f"REPLAY-TEST Model {model_version} -- not real, alerts mocked"}).scalar()

    stream_dfs = {}
    stream_signals = {}
    streams = {}
    bt_results = {}

    for ms in model_streams:
        params = ms["params"]
        tf = params.get("primary_timeframe")
        label, lot_size = ms["label"], ms["lot_size"]

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

        bt_results[label] = (run_backtest(params=params, start=start, end=end, slot_count=1,
                                          slot_mode="single", stream_name=label, lot_size_usd=lot_size), lot_size)

        with engine.begin() as conn:
            db_stream_id = conn.execute(text("""
                INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                          parameters, slot_count, slot_mode, lot_size_usd)
                VALUES (:mid, :name, :ver, 'single', CAST(:p AS jsonb), 1, 'single', :lot)
                RETURNING stream_id
            """), {"mid": sandbox_model_id, "name": label, "ver": ms["version"],
                   "p": json.dumps(params), "lot": lot_size}).scalar()

        stream_dfs[db_stream_id] = df
        stream_signals[db_stream_id] = signals
        streams[db_stream_id] = {"stream_id": db_stream_id, "model_id": sandbox_model_id, "stream_name": label,
                                  "parameters": params, "slot_count": 1, "slot_mode": "single",
                                  "lot_size_usd": lot_size}

    print(f"Model {model_version} streams loaded: "
          f"{[(s['stream_name'], s['parameters'].get('primary_timeframe')) for s in streams.values()]}", flush=True)
    for label, (res, lot_size) in bt_results.items():
        m = compute_metrics(res["trades"], lot_size, res["start"], res["end"])
        print(f"  BACKTEST reference -- {label:20s} trades={m['total_trades']:>3} ann={m['annualized_return_pct']:>7.2f}%", flush=True)

    all_ts = sorted(set().union(*[set(df.index) for df in stream_dfs.values()]))
    kraken = ReplayKraken()

    with mock.patch("src.live.blended_notifier._dispatch"), \
         mock.patch("src.live.notifier._dispatch"), \
         mock.patch("src.live.order_manager.datetime", _FakeDT):
        for i, ts in enumerate(all_ts):
            _FakeDT._now = ts.to_pydatetime().replace(tzinfo=timezone.utc)

            candle_row_this_tick = {
                sid: {"close": df.loc[ts, "close"], "low": df.loc[ts, "low"]}
                for sid, df in stream_dfs.items() if ts in df.index
            }

            for db_stream_id, df in stream_dfs.items():
                if ts not in df.index:
                    continue
                row = df.loc[ts]
                kraken.current_candle = {"open": row["open"], "high": row["high"],
                                          "low": row["low"], "close": row["close"]}
                stream = streams[db_stream_id]
                tf = stream["parameters"].get("primary_timeframe", "1h")

                with engine.begin() as conn:
                    if (order_manager.slot_is_available(conn, db_stream_id, slot_number=1)
                            and bool(stream_signals[db_stream_id].loc[ts])):
                        order_manager.place_entry(conn, stream, kraken, dry_run=False)
                    order_manager.check_pending(conn, kraken, dry_run=False)
                    position_monitor.check_all(conn, streams, candle_row_this_tick, {tf}, kraken,
                                               now=_FakeDT._now, dry_run=False)

            if i % 500 == 0:
                print(f"  ...tick {i}/{len(all_ts)} ({ts})", flush=True)

    with engine.begin() as conn:
        total_baseline = 0.0
        for db_stream_id, stream in streams.items():
            closed = conn.execute(text("""
                SELECT COUNT(*), COALESCE(SUM(realized_pnl), 0) FROM live.lots
                WHERE stream_id = :sid AND status = 'CLOSED'
            """), {"sid": db_stream_id}).fetchone()
            open_pending = conn.execute(text("""
                SELECT COUNT(*) FROM live.lots WHERE stream_id = :sid AND status IN ('OPEN', 'PENDING')
            """), {"sid": db_stream_id}).scalar()
            print(f"LIVE REPLAY -- {stream['stream_name']:20s} closed={closed[0]:>3} "
                  f"pnl=${float(closed[1]):>8.2f}  still_open/pending={open_pending}", flush=True)
            total_baseline += stream["lot_size_usd"]

        total_closed, total_pnl = conn.execute(text("""
            SELECT COUNT(*), COALESCE(SUM(ll.realized_pnl), 0) FROM live.lots ll
            JOIN live.streams ls ON ll.stream_id = ls.stream_id
            WHERE ls.model_id = :mid AND ll.status = 'CLOSED'
        """), {"mid": sandbox_model_id}).fetchone()
        print(f"\nMODEL {model_version} TOTAL -- closed={total_closed}  realized_pnl=${float(total_pnl):.2f}  "
              f"(on ${total_baseline:.0f} baseline)", flush=True)

    with engine.begin() as conn:
        _cleanup(conn, TEST_MODEL_VERSION)
    print("\nCleanup done.", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model-version", type=int, required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()
    run_replay(args.model_version, args.start, args.end)


if __name__ == "__main__":
    main()
