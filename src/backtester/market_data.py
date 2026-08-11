"""
Shared market-data access + indicator-warmup sizing.

Moved out of engine.py 2026-08-10 (docs/decisions/009) -- these two
functions have no dependency on engine.py's own simulation logic
(_run_slot/run_backtest/etc.), but several real live-execution modules
(src/live/signal_engine.py, src/live/executor.py) were importing them FROM
engine.py anyway, which meant live code depended on "the backtester" module
for no real reason. Pulling them out here is a prerequisite for engine.py
ever being deletable, independent of anything else in that ADR.
"""
import math
import os

import pandas as pd
from sqlalchemy import create_engine, text

from .indicators import _CANDLES_PER_DAY


def load_market_data(start: str = None, end: str = None) -> pd.DataFrame:
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if db_url.startswith("postgresql://") and "+psycopg2" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(db_url)

    conditions = []
    if start:
        conditions.append(f"timestamp >= '{start}'")
    if end:
        conditions.append(f"timestamp <= '{end} 23:59:59'")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT timestamp AT TIME ZONE 'UTC' AS ts, open, high, low, close, volume FROM market_data{where} ORDER BY timestamp"

    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn, parse_dates=["ts"])
    df = df.rename(columns={"ts": "timestamp"}).set_index("timestamp")
    return df


def _warmup_days(params: dict) -> int:
    """
    Compute how many extra calendar days of pre-start data are needed so that
    every indicator has a full lookback window on the first signal candle.
    """
    tf  = params.get("primary_timeframe", "15m")
    cpd = _CANDLES_PER_DAY.get(tf, 96)

    filters = params.get("filters") or {}
    core    = params.get("core_signal", "")
    core_p  = params.get("core_params") or {}

    candles = 0

    # drawdown_from_high — often the largest lookback
    dfh = filters.get("drawdown_from_high") or {}
    if dfh:
        candles = max(candles, int(dfh.get("lookback_days", 30) * cpd))

    # trend SMA filter (e.g. 200-period)
    tc = filters.get("trend_context") or {}
    if tc.get("sma_period"):
        candles = max(candles, int(tc["sma_period"]))

    # signal-specific lookbacks
    if core == "ema_crossover":
        candles = max(candles, int(core_p.get("ema_long", 50)))
    elif core == "range_breakout":
        candles = max(candles, int(core_p.get("breakout_lookback", 48)))
    elif core == "pullback_from_high":
        candles = max(candles, int(core_p.get("lookback_bars", 48)))
    elif core == "sma_pullback":
        candles = max(candles, int(core_p.get("pullback_sma", 50)))
        candles = max(candles, int(core_p.get("trend_sma", 200)))

    # volume / ATR / Bollinger filters
    vol_f = filters.get("volume") or {}
    if vol_f.get("avg_period"):
        candles = max(candles, int(vol_f["avg_period"]))
    atr_f = filters.get("atr_regime") or {}
    if atr_f.get("period"):
        candles = max(candles, int(atr_f["period"]) + int(atr_f.get("avg_period", 30)))
    bb_f = filters.get("bollinger") or {}
    if bb_f.get("period"):
        candles = max(candles, int(bb_f["period"]))
    adx_f = filters.get("adx") or {}
    if adx_f.get("period"):
        candles = max(candles, int(adx_f["period"]) * 3)  # ADX needs ~3x period to stabilize

    return math.ceil(candles / cpd) + 1  # +1 day safety buffer
