"""
Drop-in live-replay replacement for engine.py's run_backtest() -- drives the
REAL src/live/order_manager.py + src/live/position_monitor.py code against
historical market_data for ONE stream in isolation (no model composition
needed), instead of engine.py's independently-maintained simulation.

This is the piece docs/decisions/009 (unify testing with live execution)
was missing: src/app/stream_tester.py's interactive "Run/Re-run All Presets"
buttons used to call engine.py's run_backtest() directly -- the literal
"separate backtester" the ADR's mandate is about eliminating. tools/
live_replay/replay_model.py already covers a LOCKED model's composition;
tools/live_replay/replay_model1.py proved the single-stream pattern works
but only printed to stdout. This generalizes that pattern (single AND
staggered -- cascade has no live parity, see docs/decisions/009) into a
reusable function returning the same shape run_backtest() does, and
stream_tester.py now calls it for single/staggered configs (blended/
cascade/scale_down/scale_up still fall back to engine.py, flagged in the UI
as not live-validated); model_engine.run_model_backtest's fresh-run path
does the same.

Slower than run_backtest() by design -- real DB-backed order_manager/
position_monitor calls per tick, not vectorized. docs/decisions/009 item #2
profiled this tradeoff and the user chose to accept it rather than build a
second, faster-but-different code path.

SAFETY: identical pattern to tools/live_replay/*.py -- both real notifier
modules mocked for the ENTIRE call, verified via an assert BEFORE anything
else runs. Uses a reserved sandbox live.models sentinel, cleaned up in a
finally block regardless of success/failure.

Usage (mirrors run_backtest()'s signature):
    from src.backtester.live_replay_stream import run_live_replay_stream
    result = run_live_replay_stream(params, start="2022-01-01", end=None,
                                     slot_count=1, slot_mode="single",
                                     stream_name="Dip Hunter v2", lot_size_usd=20.0)
    # result["trades"] is a DataFrame in the same shape run_backtest() returns
"""
import json
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
from sqlalchemy import text

from src.app.db import get_local_engine
from src.backtester.market_data import load_market_data, _warmup_days
from src.backtester.indicators import add_indicators, resample_ohlcv
from src.backtester.signals import generate_signals
from src.data.sentiment import load_sentiment
from src.fees import MAKER_FEE, TAKER_FEE
from src.live import order_manager
from src.live import position_monitor

TEST_MODEL_VERSION = 990  # reserved sentinel, distinct from 991/992/993 (see tools/live_replay/)

_TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240}


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


def run_live_replay_stream(
    params: dict,
    start: str = None,
    end: str = None,
    slot_count: int = 1,
    slot_mode: str = "single",
    stream_name: str = "unnamed",
    lot_size_usd: float = 10.0,
    progress_cb=None,
) -> dict:
    """
    Run ONE stream through the real live order_manager/position_monitor code
    against historical data, instead of engine.py's simulation.

    slot_mode: 'single' or 'staggered' only -- 'blended' has its own
    replay_gauntlet.py; 'cascade' has no live parity (removed 2026-08-10);
    'scale_down'/'scale_up' have no live parity at all.

    progress_cb(i, total): optional, called every tick -- wire to a UI
    progress bar for a run that can take tens of seconds to minutes.

    Returns the same shape run_backtest() does: {trades, df, start, end,
    signals, maker_fee, taker_fee} -- trades is a DataFrame with entry_ts,
    exit_ts, entry_price, exit_price, capital, pnl, exit_reason,
    candles_held (mae_pct/mfe_pct are NOT tracked by live.lots per-trade,
    unlike engine.py's simulation -- compute_metrics treats their absence
    as "no MAE/MFE data," not an error).
    """
    if slot_mode not in ("single", "staggered"):
        raise ValueError(f"run_live_replay_stream does not support slot_mode={slot_mode!r} -- "
                         f"'blended' has its own replay_gauntlet.py; 'cascade' has no live parity "
                         f"(removed 2026-08-10, see docs/decisions/009); 'scale_down'/'scale_up' "
                         f"have no live parity at all")

    from tools.live_replay.replay_gauntlet import _verify_alerts_mocked
    _verify_alerts_mocked()

    tf = params.get("primary_timeframe", "1h")
    warmup = _warmup_days(params)
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup)).strftime("%Y-%m-%d") if start else None

    df_raw = load_market_data(load_start, end)
    df = resample_ohlcv(df_raw, tf)
    if params.get("sentiment"):
        fng_map = load_sentiment(load_start, end)
        df["fng_value"] = df.index.date
        df["fng_value"] = df["fng_value"].map(fng_map)
    df = add_indicators(df, params)
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    signals = generate_signals(df, params)

    engine = get_local_engine()
    with engine.begin() as conn:
        _cleanup(conn, TEST_MODEL_VERSION)
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'LIVE-REPLAY-STREAM -- not a real model, alerts mocked', 0, 'active')
            RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        db_stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, :name, 'replay', :sm, CAST(:p AS jsonb), :sc, :sm, :lot)
            RETURNING stream_id
        """), {"mid": model_id, "name": stream_name, "sm": slot_mode,
               "p": json.dumps(params), "sc": slot_count, "lot": lot_size_usd}).scalar()

    stream = {"stream_id": db_stream_id, "model_id": model_id, "stream_name": stream_name,
              "parameters": params, "slot_count": slot_count, "slot_mode": slot_mode,
              "lot_size_usd": lot_size_usd}
    streams = {db_stream_id: stream}

    # Local import to avoid a hard dependency on tools/ from src/ at module
    # load time -- ReplayKraken/its "touch-fill, not perfect-fill" semantics
    # are shared, not reimplemented.
    from tools.live_replay.replay_gauntlet import ReplayKraken
    kraken = ReplayKraken()

    total = len(df)
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
                    slot_number = order_manager.next_signal_slot(conn, stream, now=_FakeDT._now)
                    if slot_number is not None and bool(signals.loc[ts]):
                        order_manager.place_entry(conn, stream, kraken, dry_run=False, slot_number=slot_number)
                    order_manager.check_pending(conn, kraken, dry_run=False)
                    position_monitor.check_all(conn, streams, candle_row, {tf}, kraken,
                                               now=_FakeDT._now, dry_run=False)

                if progress_cb and (i % 25 == 0 or i == total - 1):
                    progress_cb(i, total)

        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT slot_number, entry_price, exit_price, opening_capital, realized_pnl,
                       exit_reason, opened_at, closed_at
                FROM live.lots WHERE model_id = :mid AND status = 'CLOSED' ORDER BY opened_at
            """), {"mid": model_id}).fetchall()

        tf_minutes = _TF_MINUTES.get(tf, 60)
        trades = pd.DataFrame([{
            "slot":         r.slot_number,
            "entry_ts":     r.opened_at,
            "exit_ts":      r.closed_at,
            "entry_price":  float(r.entry_price),
            "exit_price":   float(r.exit_price),
            "capital":      float(r.opening_capital),
            "pnl":          float(r.realized_pnl),
            "exit_reason":  r.exit_reason,
            "candles_held": int((r.closed_at - r.opened_at).total_seconds() // (tf_minutes * 60)),
        } for r in rows])

        return {
            "trades": trades, "df": df,
            "start": df.index[0] if len(df) else pd.Timestamp(start),
            "end": df.index[-1] if len(df) else pd.Timestamp(end) if end else pd.Timestamp.now(),
            "signals": signals, "maker_fee": MAKER_FEE, "taker_fee": TAKER_FEE,
        }
    finally:
        with engine.begin() as conn:
            _cleanup(conn, TEST_MODEL_VERSION)
