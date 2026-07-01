"""
Live signal engine.
Loads a stream config from live.streams and checks whether the most recently
completed candle fired a signal — using the exact same logic as backtesting.
"""
import pandas as pd

from src.backtester.engine import load_market_data, _warmup_days
from src.backtester.indicators import add_indicators, resample_ohlcv
from src.backtester.signals import generate_signals
from src.data.sentiment import load_sentiment


def check(stream: dict) -> bool:
    """
    Run signal check for a stream against the latest available data.

    stream: a dict (or Row) with at least:
        stream_name: str
        parameters:  dict — the locked stream config

    Returns True if the most recently completed candle fired a signal.
    Called only at candle close for the stream's timeframe.
    """
    params = stream["parameters"]
    tf = params.get("primary_timeframe")
    warmup = _warmup_days(params)

    # Load enough history to fully warm up all indicators
    now = pd.Timestamp.utcnow()
    load_start = (now - pd.Timedelta(days=warmup + 5)).strftime("%Y-%m-%d")

    df = load_market_data(load_start)
    if not len(df):
        return False

    if tf:
        df = resample_ohlcv(df, tf)

    if params.get("sentiment"):
        fng_map = load_sentiment(load_start)
        df["fng_value"] = df.index.normalize()
        df["fng_value"] = pd.to_datetime(df.index.date).map(
            lambda d: fng_map.get(d)
        )

    df = add_indicators(df, params)

    # Drop the current in-progress candle: the executor fires at candle close,
    # but the market_data table may include a partial candle for the new period.
    # Trim to candles whose period has fully elapsed.
    if tf and len(df) > 1:
        tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(tf, 15)
        candle_duration = pd.Timedelta(minutes=tf_minutes)
        df = df[df.index + candle_duration <= now]

    if df.empty:
        return False

    signals = generate_signals(df, params)
    return bool(signals.iloc[-1])
