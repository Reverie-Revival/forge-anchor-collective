import os
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from .indicators import add_indicators, resample_ohlcv
from .signals import generate_signals
from src.data.sentiment import load_sentiment

load_dotenv()

MAKER_FEE = 0.0025  # 0.25% per side


def load_market_data(start: str = None, end: str = None) -> pd.DataFrame:
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    # SQLAlchemy expects postgresql+psycopg2://
    if db_url.startswith("postgresql://") and "+psycopg2" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(db_url)

    conditions = []
    if start:
        conditions.append(f"timestamp >= '{start}'")
    if end:
        conditions.append(f"timestamp < '{end}'")
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
                    # record partial close (treated as a separate trade record)
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


def run_backtest(
    params: dict,
    start: str = None,
    end: str = None,
    n_slots: int = 2,
    stream_name: str = "unnamed",
) -> dict:
    """
    Run a full backtest for a single stream configuration.

    Returns a dict with:
      - trades: DataFrame of all closed trades
      - df: market data with indicators and signals
      - params: the stream config used
      - stream_name: label for display
    """
    df = load_market_data(start, end)
    primary_tf = params.get("primary_timeframe")
    if primary_tf:
        df = resample_ohlcv(df, primary_tf)

    # Join F&G sentiment if the stream uses it
    if params.get("sentiment"):
        fng_map = load_sentiment(start, end)
        df["fng_value"] = df.index.date
        df["fng_value"] = df["fng_value"].map(fng_map)

    df = add_indicators(df, params)
    signals = generate_signals(df, params)

    capital_per_slot = 10.0
    all_trades = []
    for slot in range(1, n_slots + 1):
        slot_trades = _run_slot(df, signals, params, slot, initial_capital=capital_per_slot)
        all_trades.extend(slot_trades)

    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df = trades_df.sort_values("entry_ts").reset_index(drop=True)

    return {
        "stream_name": stream_name,
        "params": params,
        "df": df,
        "signals": signals,
        "trades": trades_df,
        "start": df.index[0] if len(df) else start,
        "end": df.index[-1] if len(df) else end,
        "n_slots": n_slots,
    }
