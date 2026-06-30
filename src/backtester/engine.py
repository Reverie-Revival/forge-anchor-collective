import os
import math
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from .indicators import add_indicators, resample_ohlcv, _CANDLES_PER_DAY
from .signals import generate_signals
from src.data.sentiment import load_sentiment

load_dotenv()

MAKER_FEE = 0.0025  # 0.25% per side

# Valid slot modes. 'single' = one slot only.
# 'scale_down' = slot 2 adds when price drops below slot 1's entry (DH pattern).
# 'scale_up'   = slot 2 adds when price rises above slot 1's entry + original signal fires (MR pattern).
SLOT_MODES = ('single', 'scale_down', 'scale_up')


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

    return math.ceil(candles / cpd) + 1  # +1 day safety buffer


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


def _run_slot(df: pd.DataFrame, signals: pd.Series, params: dict, slot: int,
              initial_capital: float = 10.0, fee: float = MAKER_FEE) -> list[dict]:
    """Simulate a single slot. Returns a list of closed trade dicts."""
    position = params.get("position", {})
    trail_pct = position.get("trailing_stop_pct", 3.0) / 100.0
    expiry = position.get("entry_expiry_candles", 2)
    min_hold = position.get("min_hold_candles") or 0
    max_hold = position.get("max_hold_candles")
    partial = position.get("partial_exit")

    trades = []
    open_trade = None
    pending_entry = None  # (limit_price, candles_remaining, capital)
    slot_capital = initial_capital

    for i, (ts, row) in enumerate(df.iterrows()):
        # --- attempt pending limit fill ---
        if pending_entry and open_trade is None:
            limit_price, ttl, entry_capital = pending_entry
            if row["low"] <= limit_price <= row["high"]:
                open_trade = {
                    "entry_ts": ts,
                    "entry_price": limit_price,
                    "highest_close": limit_price,
                    "candles_held": 0,
                    "partial_done": False,
                    "capital": entry_capital,
                }
                pending_entry = None
            else:
                ttl -= 1
                if ttl <= 0:
                    pending_entry = None
                else:
                    pending_entry = (limit_price, ttl, pending_entry[2])

        # --- manage open trade ---
        if open_trade:
            open_trade["highest_close"] = max(open_trade["highest_close"], row["close"])
            open_trade["candles_held"] += 1
            stop_price = open_trade["highest_close"] * (1 - trail_pct)

            # partial exit
            if partial and not open_trade["partial_done"]:
                gain = (row["close"] - open_trade["entry_price"]) / open_trade["entry_price"]
                if gain >= partial["at_gain_pct"] / 100.0:
                    exit_pct = partial["exit_pct"] / 100.0
                    partial_capital = open_trade["capital"] * exit_pct
                    pnl = partial_capital * gain - partial_capital * fee * 2
                    trades.append({
                        "slot": slot,
                        "entry_ts": open_trade["entry_ts"],
                        "exit_ts": ts,
                        "entry_price": open_trade["entry_price"],
                        "exit_price": row["close"],
                        "capital": partial_capital,
                        "pnl": pnl,
                        "exit_reason": "partial",
                        "candles_held": open_trade["candles_held"],
                    })
                    open_trade["capital"] *= (1 - exit_pct)
                    open_trade["partial_done"] = True

            # check exit conditions (respect min hold)
            if open_trade["candles_held"] >= min_hold:
                exit_price = None
                exit_reason = None

                if max_hold and open_trade["candles_held"] >= max_hold:
                    exit_price = row["close"]
                    exit_reason = "max_hold"
                elif row["low"] <= stop_price:
                    exit_price = stop_price
                    exit_reason = "trailing_stop"

                if exit_price:
                    gain = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"]
                    pnl = open_trade["capital"] * gain - open_trade["capital"] * fee * 2
                    trades.append({
                        "slot": slot,
                        "entry_ts": open_trade["entry_ts"],
                        "exit_ts": ts,
                        "entry_price": open_trade["entry_price"],
                        "exit_price": exit_price,
                        "capital": open_trade["capital"],
                        "pnl": pnl,
                        "exit_reason": exit_reason,
                        "candles_held": open_trade["candles_held"],
                    })
                    open_trade = None
                    slot_capital += pnl

        # --- check for new signal ---
        if open_trade is None and pending_entry is None and signals.iloc[i] and slot_capital > 0.01:
            limit_price = row["close"]
            pending_entry = (limit_price, expiry, slot_capital)

    # close any open trade at end of data
    if open_trade:
        last_row = df.iloc[-1]
        gain = (last_row["close"] - open_trade["entry_price"]) / open_trade["entry_price"]
        pnl = open_trade["capital"] * gain - open_trade["capital"] * MAKER_FEE * 2
        trades.append({
            "slot": slot,
            "entry_ts": open_trade["entry_ts"],
            "exit_ts": df.index[-1],
            "entry_price": open_trade["entry_price"],
            "exit_price": last_row["close"],
            "capital": open_trade["capital"],
            "pnl": pnl,
            "exit_reason": "end_of_data",
            "candles_held": open_trade["candles_held"],
        })

    return trades


def _build_slot1_state(df: pd.DataFrame, slot1_trades: list) -> tuple[pd.Series, pd.Series]:
    """
    From completed slot 1 trades, build vectorized state series.
    Returns (open_mask, entry_prices):
      open_mask    — bool Series, True during any slot 1 open position
      entry_prices — float Series, slot 1's entry price during open positions, NaN otherwise
    """
    open_mask    = pd.Series(False,         index=df.index)
    entry_prices = pd.Series(float('nan'),  index=df.index)

    for trade in slot1_trades:
        mask = (df.index >= trade["entry_ts"]) & (df.index <= trade["exit_ts"])
        open_mask.loc[mask]    = True
        entry_prices.loc[mask] = trade["entry_price"]

    return open_mask, entry_prices


def _derive_slot2_signals(
    df: pd.DataFrame,
    slot1_trades: list,
    slot_mode: str,
    params: dict,
    base_signals: pd.Series,
) -> pd.Series:
    """
    Derive slot 2 entry signals from slot 1's trade history.

    scale_down: enter when slot 1 is open AND price drops >= slot2_trigger_pct below entry.
    scale_up:   enter when slot 1 is open AND price rises >= slot2_trigger_pct above entry
                AND the original signal also fires (confirm trend, not chase).
    """
    trigger_pct = params.get("position", {}).get("slot2_trigger_pct", 3.0) / 100.0
    open_mask, entry_prices = _build_slot1_state(df, slot1_trades)

    if slot_mode == 'scale_down':
        return open_mask & (df["close"] <= entry_prices * (1 - trigger_pct))

    if slot_mode == 'scale_up':
        return open_mask & (df["close"] >= entry_prices * (1 + trigger_pct)) & base_signals

    return pd.Series(False, index=df.index)


def run_backtest(
    params: dict,
    start: str = None,
    end: str = None,
    slot_count: int = 1,
    slot_mode: str = 'single',
    stream_name: str = "unnamed",
    lot_size_usd: float = 10.0,
) -> dict:
    """
    Run a full backtest for a single stream configuration.

    slot_mode controls how multiple slots enter:
      'single'     — one slot, lot_size_usd capital
      'scale_down' — slot 2 adds when price drops X% below slot 1's entry (see slot2_trigger_pct in params.position)
      'scale_up'   — slot 2 adds when price rises X% above slot 1's entry AND signal fires

    Returns a dict with trades DataFrame, market data, params, and metadata.
    """
    if slot_mode not in SLOT_MODES:
        raise ValueError(f"slot_mode must be one of {SLOT_MODES}, got '{slot_mode}'")

    primary_tf = params.get("primary_timeframe")
    warmup     = _warmup_days(params)
    load_start = (
        (pd.Timestamp(start) - pd.Timedelta(days=warmup)).strftime("%Y-%m-%d")
        if start and warmup > 0 else start
    )

    df = load_market_data(load_start, end)
    if primary_tf:
        df = resample_ohlcv(df, primary_tf)

    if params.get("sentiment"):
        fng_map = load_sentiment(load_start, end)
        df["fng_value"] = df.index.date
        df["fng_value"] = df["fng_value"].map(fng_map)

    df = add_indicators(df, params)

    if start:
        clip_ts = pd.Timestamp(start)
        df = df[df.index >= clip_ts]

    signals = generate_signals(df, params)

    # --- Slot dispatch ---
    slot1_trades = _run_slot(df, signals, params, slot=1, initial_capital=lot_size_usd)
    all_trades = slot1_trades

    if slot_count >= 2 and slot_mode in ('scale_down', 'scale_up'):
        slot2_signals = _derive_slot2_signals(df, slot1_trades, slot_mode, params, signals)
        slot2_trades  = _run_slot(df, slot2_signals, params, slot=2, initial_capital=lot_size_usd)
        all_trades    = slot1_trades + slot2_trades

    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df = trades_df.sort_values("entry_ts").reset_index(drop=True)

    return {
        "stream_name": stream_name,
        "params":      params,
        "df":          df,
        "signals":     signals,
        "trades":      trades_df,
        "start":       df.index[0] if len(df) else start,
        "end":         df.index[-1] if len(df) else end,
        "slot_count":  slot_count,
        "slot_mode":   slot_mode,
    }
