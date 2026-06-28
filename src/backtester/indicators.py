import pandas as pd
import numpy as np

_RESAMPLE_RULES = {"1h": "1h", "4h": "4h", "1d": "1D"}


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample 15m OHLCV data up to a coarser timeframe."""
    rule = _RESAMPLE_RULES.get(timeframe)
    if not rule:
        raise ValueError(f"Unsupported primary_timeframe '{timeframe}'. Use: {list(_RESAMPLE_RULES)}")
    resampled = df.resample(rule).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()
    return resampled


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.ewm(com=period - 1, adjust=False).mean()


def volume_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).max()


def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Compute all indicators required by a stream's parameter config."""
    df = df.copy()

    core = params.get("core_signal")
    core_p = params.get("core_params", {})
    filters = params.get("filters") or {}
    tc = filters.get("trend_context") or {}
    rsi_f = filters.get("rsi") or {}
    vol_f = filters.get("volume") or {}
    atr_f = filters.get("atr_regime") or {}
    tf_conf = params.get("timeframe_confirmation") or {}

    if core == "ema_crossover":
        df["ema_short"] = ema(df["close"], core_p["ema_short"])
        df["ema_long"] = ema(df["close"], core_p["ema_long"])

    if core == "rsi_dip":
        df["rsi"] = rsi(df["close"], core_p.get("rsi_period", 14))
        df["sma_dip"] = sma(df["close"], core_p.get("sma_period", 20))

    if core == "range_breakout":
        lookback = core_p.get("breakout_lookback", 48)
        df["breakout_high"] = rolling_high(df["high"], lookback).shift(1)

    if core == "volume_surge":
        avg_period = core_p.get("volume_avg_period", 20)
        df["volume_avg"] = volume_sma(df["volume"], avg_period)
        df["rsi"] = rsi(df["close"], 14)

    if core == "sma_pullback":
        df["sma_pullback"] = sma(df["close"], core_p.get("pullback_sma", 50))

    if tc.get("sma_period"):
        col = f"trend_sma_{tc['sma_period']}"
        df[col] = sma(df["close"], tc["sma_period"])

    if rsi_f.get("period") and "rsi" not in df.columns:
        df["rsi"] = rsi(df["close"], rsi_f["period"])

    if vol_f.get("avg_period") and "volume_avg" not in df.columns:
        df["volume_avg"] = volume_sma(df["volume"], vol_f["avg_period"])

    if atr_f.get("period"):
        df["atr"] = atr(df, atr_f["period"])
        df["atr_avg"] = sma(df["atr"], atr_f.get("avg_period", 30))

    if tf_conf.get("sma_period"):
        col = f"tf_sma_{tf_conf['sma_period']}"
        df[col] = sma(df["close"], tf_conf["sma_period"])

    return df
