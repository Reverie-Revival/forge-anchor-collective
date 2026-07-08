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
# 'staggered'  = N independent slots, round-robin dispatch, optional gap + capital weights.
# 'scale_down' = slot 2 adds when price drops below slot 1's entry (2-slot only).
# 'scale_up'   = slot 2 adds when price rises above slot 1's entry + signal fires (2-slot only).
# 'cascade'    = N slots; slot 1 fires on signal, each subsequent slot auto-fires when price
#                drops cascade_drop_pct below the previous slot's entry (params.position).
SLOT_MODES = ('single', 'staggered', 'scale_down', 'scale_up', 'cascade')


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
    trail_pct = position.get("trailing_stop_pct")
    trail_atr_mult = position.get("trailing_stop_atr_multiplier")
    stop_loss_pct = position.get("stop_loss_pct")
    take_profit_pct = position.get("take_profit_pct")
    trail_tighten = position.get("trail_step_tighten")  # {at_gain_pct, tighten_to_pct}
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
                    "lowest_low": limit_price,
                    "highest_high": limit_price,
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
            open_trade["lowest_low"]    = min(open_trade["lowest_low"],    row["low"])
            open_trade["highest_high"]  = max(open_trade["highest_high"],  row["high"])
            open_trade["candles_held"] += 1

            # Step-tighten: upgrade trail once trade reaches threshold gain
            current_gain_pct = (open_trade["highest_close"] - open_trade["entry_price"]) / open_trade["entry_price"] * 100
            if trail_tighten and not open_trade.get("trail_tightened") and current_gain_pct >= trail_tighten["at_gain_pct"]:
                open_trade["trail_tightened"] = True
            effective_trail = (trail_tighten["tighten_to_pct"] if open_trade.get("trail_tightened") and trail_tighten else trail_pct)

            # Trailing stop (from peak)
            if trail_atr_mult and "atr" in row.index and not pd.isna(row["atr"]):
                trail_stop = open_trade["highest_close"] - trail_atr_mult * row["atr"]
            elif effective_trail:
                trail_stop = open_trade["highest_close"] * (1 - effective_trail / 100.0)
            else:
                trail_stop = None

            # Hard stop loss (from entry — never moves)
            hard_stop = open_trade["entry_price"] * (1 - stop_loss_pct / 100.0) if stop_loss_pct else None

            # Take profit ceiling (from entry — exits when high touches target)
            take_profit_price = open_trade["entry_price"] * (1 + take_profit_pct / 100.0) if take_profit_pct else None

            # Use the more protective (higher) of the two active stops
            candidates = [s for s in [trail_stop, hard_stop] if s is not None]
            stop_price = max(candidates) if candidates else open_trade["highest_close"] * 0.97

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
                elif take_profit_price and row["high"] >= take_profit_price:
                    exit_price = take_profit_price
                    exit_reason = "take_profit"
                elif row["low"] <= stop_price:
                    exit_price = stop_price
                    # distinguish which stop fired
                    if hard_stop and stop_price <= hard_stop:
                        exit_reason = "stop_loss"
                    else:
                        exit_reason = "trailing_stop"

                if exit_price:
                    gain = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"]
                    pnl  = open_trade["capital"] * gain - open_trade["capital"] * fee * 2
                    ep   = open_trade["entry_price"]
                    trades.append({
                        "slot":          slot,
                        "entry_ts":      open_trade["entry_ts"],
                        "exit_ts":       ts,
                        "entry_price":   ep,
                        "exit_price":    exit_price,
                        "highest_close": open_trade["highest_close"],
                        "capital":       open_trade["capital"],
                        "pnl":           pnl,
                        "exit_reason":   exit_reason,
                        "candles_held":  open_trade["candles_held"],
                        "mae_pct":       (ep - open_trade["lowest_low"])  / ep * 100,
                        "mfe_pct":       (open_trade["highest_high"] - ep) / ep * 100,
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
        pnl  = open_trade["capital"] * gain - open_trade["capital"] * MAKER_FEE * 2
        ep   = open_trade["entry_price"]
        trades.append({
            "slot":          slot,
            "entry_ts":      open_trade["entry_ts"],
            "exit_ts":       df.index[-1],
            "entry_price":   ep,
            "exit_price":    last_row["close"],
            "highest_close": open_trade["highest_close"],
            "capital":       open_trade["capital"],
            "pnl":           pnl,
            "exit_reason":   "end_of_data",
            "candles_held":  open_trade["candles_held"],
            "mae_pct":       (ep - open_trade["lowest_low"])  / ep * 100,
            "mfe_pct":       (open_trade["highest_high"] - ep) / ep * 100,
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


def _run_staggered_slots(
    df: pd.DataFrame,
    signals: pd.Series,
    params: dict,
    slot_count: int,
    total_capital: float,
    fee: float = MAKER_FEE,
) -> list[dict]:
    """
    Run multiple independent staggered slots.

    Each signal goes to whichever slot has been free the longest (round-robin by
    last-freed candle index). Slots never enter the same signal simultaneously.

    Slot config comes from params["slots"]:
      slot_entry_gap_candles — min candles between any two entries across all slots
      slot_capital_weight    — e.g. [70, 30]; weights sum to 100; default = equal split
    """
    slots_conf = params.get("slots") or {}
    gap = int(slots_conf.get("slot_entry_gap_candles", 0))
    weights = slots_conf.get("slot_capital_weight")

    if weights and len(weights) >= slot_count:
        total_w = sum(weights[:slot_count])
        slot_capitals = [total_capital * w / total_w for w in weights[:slot_count]]
    else:
        slot_capitals = [total_capital / slot_count] * slot_count

    slots = [
        {
            "slot_number":      i + 1,
            "open_trade":       None,
            "pending_entry":    None,
            "capital":          slot_capitals[i],
            "last_freed_candle": -1,   # -1 = never occupied; sorts to front (longest free)
            "last_entry_candle": -1,
        }
        for i in range(slot_count)
    ]

    all_trades = []
    position    = params.get("position", {})
    trail_pct   = position.get("trailing_stop_pct")
    trail_atr_mult = position.get("trailing_stop_atr_multiplier")
    stop_loss_pct = position.get("stop_loss_pct")
    expiry      = position.get("entry_expiry_candles", 2)
    min_hold    = position.get("min_hold_candles") or 0
    max_hold    = position.get("max_hold_candles")
    partial_conf = position.get("partial_exit")
    last_global_entry = -1

    for i, (ts, row) in enumerate(df.iterrows()):
        for slot in slots:
            # attempt pending fill
            if slot["pending_entry"] and slot["open_trade"] is None:
                lp, ttl, cap = slot["pending_entry"]
                if row["low"] <= lp <= row["high"]:
                    slot["open_trade"] = {
                        "entry_ts": ts, "entry_price": lp,
                        "highest_close": lp, "lowest_low": lp, "highest_high": lp,
                        "candles_held": 0, "partial_done": False, "capital": cap,
                    }
                    slot["pending_entry"] = None
                else:
                    ttl -= 1
                    if ttl <= 0:
                        slot["pending_entry"] = None
                        slot["last_freed_candle"] = i
                    else:
                        slot["pending_entry"] = (lp, ttl, cap)

            # manage open trade
            if slot["open_trade"]:
                t = slot["open_trade"]
                t["highest_close"] = max(t["highest_close"], row["close"])
                t["lowest_low"]    = min(t["lowest_low"],    row["low"])
                t["highest_high"]  = max(t["highest_high"],  row["high"])
                t["candles_held"] += 1

                if trail_atr_mult and "atr" in row.index and not pd.isna(row["atr"]):
                    trail_stop = t["highest_close"] - trail_atr_mult * row["atr"]
                elif trail_pct:
                    trail_stop = t["highest_close"] * (1 - trail_pct / 100.0)
                else:
                    trail_stop = None

                hard_stop = t["entry_price"] * (1 - stop_loss_pct / 100.0) if stop_loss_pct else None
                candidates = [s for s in [trail_stop, hard_stop] if s is not None]
                stop_price = max(candidates) if candidates else t["highest_close"] * 0.97

                if partial_conf and not t["partial_done"]:
                    gain = (row["close"] - t["entry_price"]) / t["entry_price"]
                    if gain >= partial_conf["at_gain_pct"] / 100.0:
                        ep = partial_conf["exit_pct"] / 100.0
                        pcap = t["capital"] * ep
                        pnl = pcap * gain - pcap * fee * 2
                        all_trades.append({
                            "slot": slot["slot_number"], "entry_ts": t["entry_ts"],
                            "exit_ts": ts, "entry_price": t["entry_price"],
                            "exit_price": row["close"], "capital": pcap,
                            "pnl": pnl, "exit_reason": "partial",
                            "candles_held": t["candles_held"],
                        })
                        t["capital"] *= (1 - ep)
                        t["partial_done"] = True

                if t["candles_held"] >= min_hold:
                    exit_price, exit_reason = None, None
                    if max_hold and t["candles_held"] >= max_hold:
                        exit_price, exit_reason = row["close"], "max_hold"
                    elif row["low"] <= stop_price:
                        exit_price = stop_price
                        if hard_stop and stop_price <= hard_stop:
                            exit_reason = "stop_loss"
                        else:
                            exit_reason = "trailing_stop"

                    if exit_price:
                        gain = (exit_price - t["entry_price"]) / t["entry_price"]
                        pnl  = t["capital"] * gain - t["capital"] * fee * 2
                        ep   = t["entry_price"]
                        all_trades.append({
                            "slot":          slot["slot_number"],
                            "entry_ts":      t["entry_ts"],
                            "exit_ts":       ts,
                            "entry_price":   ep,
                            "exit_price":    exit_price,
                            "highest_close": t["highest_close"],
                            "capital":       t["capital"],
                            "pnl":           pnl,
                            "exit_reason":   exit_reason,
                            "candles_held":  t["candles_held"],
                            "mae_pct":       (ep - t["lowest_low"])  / ep * 100,
                            "mfe_pct":       (t["highest_high"] - ep) / ep * 100,
                        })
                        slot["capital"] += pnl
                        slot["open_trade"] = None
                        slot["last_freed_candle"] = i

        # dispatch signal to longest-free slot (gap enforced globally)
        if signals.iloc[i] and (gap == 0 or (i - last_global_entry) >= gap):
            free_slots = sorted(
                [s for s in slots
                 if s["open_trade"] is None and s["pending_entry"] is None
                 and s["capital"] > 0.01],
                key=lambda s: s["last_freed_candle"],
            )
            if free_slots:
                chosen = free_slots[0]
                chosen["pending_entry"] = (row["close"], expiry, chosen["capital"])
                chosen["last_entry_candle"] = i
                last_global_entry = i

    # close open trades at end of data
    for slot in slots:
        if slot["open_trade"]:
            t = slot["open_trade"]
            last_row = df.iloc[-1]
            ep   = t["entry_price"]
            gain = (last_row["close"] - ep) / ep
            pnl  = t["capital"] * gain - t["capital"] * MAKER_FEE * 2
            all_trades.append({
                "slot":          slot["slot_number"],
                "entry_ts":      t["entry_ts"],
                "exit_ts":       df.index[-1],
                "entry_price":   ep,
                "exit_price":    last_row["close"],
                "highest_close": t["highest_close"],
                "capital":       t["capital"],
                "pnl":           pnl,
                "exit_reason":   "end_of_data",
                "candles_held":  t["candles_held"],
            })

    return all_trades


def _run_cascade_slots(
    df: pd.DataFrame,
    signals: pd.Series,
    params: dict,
    slot_count: int,
    total_capital: float,
    fee: float = MAKER_FEE,
) -> list[dict]:
    """
    Cascade DCA entry: Slot 1 fires on the base signal.
    Each subsequent slot auto-enters when the previous slot is open AND price
    drops cascade_drop_pct below that slot's entry price.
    Each slot has its own trailing stop and hard stop — exits are independent.
    When a slot exits its position, the cascade trigger for the next slot is cleared.
    """
    position        = params.get("position", {})
    trail_pct       = position.get("trailing_stop_pct")
    trail_steps     = position.get("trailing_stop_steps")  # [[gain_pct, trail_pct], ...] ascending
    stop_loss_pct   = position.get("stop_loss_pct")
    cascade_drop    = position.get("cascade_drop_pct", 5.0) / 100.0
    expiry          = position.get("entry_expiry_candles", 2)
    min_hold        = position.get("min_hold_candles") or 0
    max_hold        = position.get("max_hold_candles")
    slot_capital    = total_capital / slot_count

    slots = [
        {
            "idx":              i,
            "slot_number":      i + 1,
            "open_trade":       None,
            "pending_entry":    None,
            "capital":          slot_capital,
            "cascade_trigger":  None,   # price level that auto-fires this slot
        }
        for i in range(slot_count)
    ]

    all_trades = []

    for i, (ts, row) in enumerate(df.iterrows()):

        # ── 1. Fill pending entries + manage open trades ─────────────────────
        for slot in slots:
            # try to fill pending limit order
            if slot["pending_entry"] and slot["open_trade"] is None:
                lp, ttl, cap = slot["pending_entry"]
                if row["low"] <= lp <= row["high"]:
                    slot["open_trade"] = {
                        "entry_ts":     ts,
                        "entry_price":  lp,
                        "highest_close": lp,
                        "lowest_low":   lp,
                        "highest_high": lp,
                        "candles_held": 0,
                        "capital":      cap,
                    }
                    slot["pending_entry"] = None
                    # arm the cascade trigger for the next slot
                    nxt = slot["idx"] + 1
                    if nxt < slot_count:
                        slots[nxt]["cascade_trigger"] = lp * (1 - cascade_drop)
                else:
                    ttl -= 1
                    slot["pending_entry"] = (lp, ttl, cap) if ttl > 0 else None

            # manage open trade
            if slot["open_trade"]:
                t = slot["open_trade"]
                t["highest_close"] = max(t["highest_close"], row["close"])
                t["lowest_low"]    = min(t["lowest_low"],    row["low"])
                t["highest_high"]  = max(t["highest_high"],  row["high"])
                t["candles_held"] += 1

                if trail_pct:
                    eff_trail = trail_pct
                    if trail_steps:
                        gain_pct = (t["highest_close"] - t["entry_price"]) / t["entry_price"] * 100
                        for threshold, tighter in sorted(trail_steps, key=lambda x: x[0]):
                            if gain_pct >= threshold:
                                eff_trail = tighter
                    trail_stop = t["highest_close"] * (1 - eff_trail / 100.0)
                else:
                    trail_stop = None
                hard_stop  = t["entry_price"]   * (1 - stop_loss_pct / 100.0) if stop_loss_pct else None
                candidates = [s for s in [trail_stop, hard_stop] if s is not None]
                stop_price = max(candidates) if candidates else t["highest_close"] * 0.97

                if t["candles_held"] >= min_hold:
                    exit_price = exit_reason = None
                    if max_hold and t["candles_held"] >= max_hold:
                        exit_price, exit_reason = row["close"], "max_hold"
                    elif row["low"] <= stop_price:
                        exit_price = stop_price
                        exit_reason = "stop_loss" if (hard_stop and stop_price <= hard_stop) else "trailing_stop"

                    if exit_price:
                        gain = (exit_price - t["entry_price"]) / t["entry_price"]
                        pnl  = t["capital"] * gain - t["capital"] * fee * 2
                        ep   = t["entry_price"]
                        all_trades.append({
                            "slot":          slot["slot_number"],
                            "entry_ts":      t["entry_ts"],
                            "exit_ts":       ts,
                            "entry_price":   ep,
                            "exit_price":    exit_price,
                            "highest_close": t["highest_close"],
                            "capital":       t["capital"],
                            "pnl":           pnl,
                            "exit_reason":   exit_reason,
                            "candles_held":  t["candles_held"],
                            "mae_pct":       (ep - t["lowest_low"])  / ep * 100,
                            "mfe_pct":       (t["highest_high"] - ep) / ep * 100,
                        })
                        slot["capital"]    += pnl
                        slot["open_trade"]  = None
                        # disarm cascade trigger for the next slot
                        nxt = slot["idx"] + 1
                        if nxt < slot_count:
                            slots[nxt]["cascade_trigger"] = None

        # ── 2. Slot 0: base signal entry ─────────────────────────────────────
        s0 = slots[0]
        if (signals.iloc[i]
                and s0["open_trade"] is None
                and s0["pending_entry"] is None
                and s0["capital"] > 0.01):
            s0["pending_entry"] = (row["close"], expiry, s0["capital"])

        # ── 3. Slots 1+: cascade trigger check ───────────────────────────────
        for idx in range(1, slot_count):
            slot      = slots[idx]
            prev_slot = slots[idx - 1]
            if (slot["cascade_trigger"] is not None
                    and prev_slot["open_trade"] is not None   # anchor must still be open
                    and slot["open_trade"] is None
                    and slot["pending_entry"] is None
                    and slot["capital"] > 0.01
                    and row["close"] <= slot["cascade_trigger"]):
                slot["pending_entry"]   = (row["close"], expiry, slot["capital"])
                slot["cascade_trigger"] = None  # consumed; will re-arm on fill

    # ── Close any open trades at end of data ─────────────────────────────────
    for slot in slots:
        if slot["open_trade"]:
            t        = slot["open_trade"]
            last_row = df.iloc[-1]
            ep       = t["entry_price"]
            gain     = (last_row["close"] - ep) / ep
            pnl      = t["capital"] * gain - t["capital"] * MAKER_FEE * 2
            all_trades.append({
                "slot":          slot["slot_number"],
                "entry_ts":      t["entry_ts"],
                "exit_ts":       df.index[-1],
                "entry_price":   ep,
                "exit_price":    last_row["close"],
                "highest_close": t["highest_close"],
                "capital":       t["capital"],
                "pnl":           pnl,
                "exit_reason":   "end_of_data",
                "candles_held":  t["candles_held"],
                "mae_pct":       (ep - t["lowest_low"])  / ep * 100,
                "mfe_pct":       (t["highest_high"] - ep) / ep * 100,
            })

    return all_trades


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
    if slot_mode == 'staggered' and slot_count >= 2:
        all_trades = _run_staggered_slots(df, signals, params, slot_count, lot_size_usd)
    elif slot_mode == 'cascade' and slot_count >= 2:
        all_trades = _run_cascade_slots(df, signals, params, slot_count, lot_size_usd)
    else:
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
