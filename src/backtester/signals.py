import pandas as pd


def _check_filters(row: pd.Series, prev_row: pd.Series, params: dict) -> bool:
    """Return False if any enabled filter fails."""
    filters = params.get("filters") or {}

    tc = filters.get("trend_context") or {}
    if tc:
        col = f"trend_sma_{tc['sma_period']}"
        if col in row.index:
            if tc.get("require") == "above" and row["close"] <= row[col]:
                return False
            if tc.get("require") == "below" and row["close"] >= row[col]:
                return False

    rsi_f = filters.get("rsi") or {}
    if rsi_f and "rsi" in row.index:
        if rsi_f.get("min") is not None and row["rsi"] < rsi_f["min"]:
            return False
        if rsi_f.get("max") is not None and row["rsi"] > rsi_f["max"]:
            return False

    vol_f = filters.get("volume") or {}
    if vol_f and "volume_avg" in row.index:
        threshold = row["volume_avg"] * vol_f.get("min_multiplier", 1.0)
        if row["volume"] < threshold:
            return False

    atr_f = filters.get("atr_regime") or {}
    if atr_f and "atr" in row.index and "atr_avg" in row.index:
        max_pct = atr_f.get("max_pct_of_avg", 100) / 100.0
        if row["atr"] > row["atr_avg"] * max_pct:
            return False

    sentiment = params.get("sentiment") or {}
    fng = sentiment.get("fear_greed") or {}
    if fng and "fng_value" in row.index and not pd.isna(row["fng_value"]):
        val = row["fng_value"]
        if fng.get("min") is not None and val < fng["min"]:
            return False
        if fng.get("max") is not None and val > fng["max"]:
            return False

    dfh_f = filters.get("drawdown_from_high") or {}
    if dfh_f and "drawdown_from_high_pct" in row.index and not pd.isna(row["drawdown_from_high_pct"]):
        if row["drawdown_from_high_pct"] > -(dfh_f.get("min_drop_pct", 15.0)):
            return False

    bb_f = filters.get("bollinger") or {}
    if bb_f and "bb_bandwidth" in row.index and not pd.isna(row["bb_bandwidth"]):
        squeeze = bb_f.get("squeeze") or {}
        if squeeze.get("max_bandwidth_pct") is not None:
            if row["bb_bandwidth"] > squeeze["max_bandwidth_pct"]:
                return False

    bc_f = filters.get("breakout_candle") or {}
    if bc_f:
        candle_range = row["high"] - row["low"]
        if candle_range > 0:
            if bc_f.get("body_ratio_min") is not None:
                body = abs(row["close"] - row["open"])
                if body / candle_range < bc_f["body_ratio_min"]:
                    return False
            if bc_f.get("close_position_min") is not None:
                close_pos = (row["close"] - row["low"]) / candle_range
                if close_pos < bc_f["close_position_min"]:
                    return False

    atr_f = filters.get("atr_regime") or {}
    if atr_f.get("min_consecutive_candles") and "atr_low_streak" in row.index and not pd.isna(row["atr_low_streak"]):
        if row["atr_low_streak"] < atr_f["min_consecutive_candles"]:
            return False

    return True


def generate_signals(df: pd.DataFrame, params: dict) -> pd.Series:
    """
    Returns a boolean Series — True on candles where all entry conditions are met.
    Requires indicators to already be computed on df (via add_indicators).
    """
    core = params.get("core_signal")
    core_p = params.get("core_params", {})
    signals = pd.Series(False, index=df.index)

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if pd.isna(row["close"]):
            continue

        fired = False

        if core == "ema_crossover":
            if pd.isna(row["ema_short"]) or pd.isna(row["ema_long"]):
                continue
            if pd.isna(prev["ema_short"]) or pd.isna(prev["ema_long"]):
                continue
            crossed = (prev["ema_short"] <= prev["ema_long"]) and (row["ema_short"] > row["ema_long"])
            fired = crossed

        elif core == "rsi_dip":
            if pd.isna(row.get("rsi")) or pd.isna(row.get("sma_dip")):
                continue
            rsi_ok = row["rsi"] < core_p.get("rsi_threshold", 35)
            dip_pct = core_p.get("dip_pct", 2.0) / 100.0
            dip_ok = row["close"] < row["sma_dip"] * (1 - dip_pct)
            fired = rsi_ok and dip_ok

        elif core == "range_breakout":
            if pd.isna(row.get("breakout_high")):
                continue
            fired = row["close"] > row["breakout_high"]

        elif core == "volume_surge":
            if pd.isna(row.get("volume_avg")) or pd.isna(row.get("rsi")):
                continue
            multiplier = core_p.get("volume_multiplier", 2.5)
            vol_ok = row["volume"] > row["volume_avg"] * multiplier
            bullish = row["close"] > row["open"]
            rsi_min = params.get("filters", {}).get("rsi", {}) or {}
            rsi_ok = True
            if rsi_min.get("min") is not None:
                rsi_ok = rsi_ok and row["rsi"] >= rsi_min["min"]
            if rsi_min.get("max") is not None:
                rsi_ok = rsi_ok and row["rsi"] <= rsi_min["max"]
            fired = vol_ok and bullish and rsi_ok

        elif core == "rsi_recovery":
            # Fires on the candle where RSI crosses back UP through the threshold.
            # Previous candle oversold, current candle recovering — the snap-back entry.
            # Optional: require_bullish_candle confirms price is already moving up on entry.
            threshold = core_p.get("rsi_threshold", 35)
            if pd.isna(row.get("rsi")) or pd.isna(prev.get("rsi")):
                continue
            fired = prev["rsi"] < threshold and row["rsi"] >= threshold
            if fired and core_p.get("require_bullish_candle", False):
                fired = row["close"] > prev["close"]

        elif core == "fear_dip":
            dip_pct = core_p.get("dip_pct", 3.0) / 100.0
            sma_period = core_p.get("sma_period")
            if sma_period:
                if pd.isna(row.get("sma_dip")):
                    continue
                fired = row["close"] < row["sma_dip"] * (1 - dip_pct)
            else:
                if pd.isna(prev.get("close")):
                    continue
                fired = row["close"] < prev["close"] * (1 - dip_pct)

        elif core == "sma_pullback":
            if pd.isna(row.get("sma_pullback")):
                continue
            trend_col = f"trend_sma_{core_p.get('trend_sma', 200)}"
            if trend_col not in row.index or pd.isna(row[trend_col]):
                continue
            in_uptrend = row["close"] > row[trend_col]
            tol = core_p.get("pullback_tolerance_pct", 1.5) / 100.0
            near_sma = abs(row["close"] - row["sma_pullback"]) / row["sma_pullback"] <= tol
            above_sma = row["close"] >= row["sma_pullback"]
            bouncing = row["close"] > prev["close"]
            fired = in_uptrend and near_sma and above_sma and bouncing

        if fired and _check_filters(row, prev, params):
            signals.iloc[i] = True

    return signals
