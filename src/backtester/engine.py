import os
import math
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from .indicators import add_indicators, resample_ohlcv, _CANDLES_PER_DAY
from .signals import generate_signals
from .slot_math import slot_capitals_for, tilted_slot_weights
from src.data.sentiment import load_sentiment
from src.fees import MAKER_FEE, TAKER_FEE

load_dotenv()

# MAKER_FEE/TAKER_FEE come from src/fees.py -- the same values src/live/order_manager.py
# uses for real trading. Every slot mode here uses a limit order to enter and a
# market order to exit (see CLAUDE.md "Key Constraints"), so round-trip cost is
# MAKER_FEE + TAKER_FEE, never a symmetric fee*2 -- a market exit always pays
# the taker rate. Every function below also accepts maker_fee/taker_fee overrides
# so a backtest can be re-run under a hypothetical rate without editing code.

# Valid slot modes. 'single' = one slot only.
# 'staggered'  = N independent slots, round-robin dispatch, optional gap + capital weights.
# 'scale_down' = slot 2 adds when price drops below slot 1's entry (2-slot only).
# 'scale_up'   = slot 2 adds when price rises above slot 1's entry + signal fires (2-slot only).
# 'cascade'    = N slots; slot 1 fires on signal, each subsequent slot auto-fires when price
#                drops cascade_drop_pct below the previous slot's entry (params.position).
# 'blended'    = N slots, ONE position with a single weighted-average cost basis. Adds fire
#                at cumulative_drop_pcts (from the original entry, not the prior add) in
#                params.position. One shared exit off the blended average -- no per-slot stops.
SLOT_MODES = ('single', 'staggered', 'scale_down', 'scale_up', 'cascade', 'blended')


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
              initial_capital: float = 10.0,
              maker_fee: float = MAKER_FEE, taker_fee: float = TAKER_FEE) -> list[dict]:
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
                    pnl = partial_capital * gain - partial_capital * (maker_fee + taker_fee)
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
                    # Model 1's real live order_manager.py places an unconditional
                    # MARKET sell once triggered (a deliberate choice -- see plan
                    # notes -- a real stop-loss should guarantee execution, not rest
                    # as an unfilled limit). A market sell doesn't get to choose its
                    # price: if the whole candle kept falling past the trigger, real
                    # execution lands closer to the close, not at the idealized
                    # trigger level -- crediting stop_price outright regardless of
                    # where the candle actually ended is the same "unreachable
                    # price" optimism found (and fixed differently, via a real
                    # limit order) in the blended engine. min() here never assumes
                    # a fill better than the candle's own close.
                    exit_price = min(stop_price, row["close"])
                    # distinguish which stop fired
                    if hard_stop and stop_price <= hard_stop:
                        exit_reason = "stop_loss"
                    else:
                        exit_reason = "trailing_stop"

                if exit_price:
                    gain = (exit_price - open_trade["entry_price"]) / open_trade["entry_price"]
                    pnl  = open_trade["capital"] * gain - open_trade["capital"] * (maker_fee + taker_fee)
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
        pnl  = open_trade["capital"] * gain - open_trade["capital"] * (maker_fee + taker_fee)
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
    maker_fee: float = MAKER_FEE,
    taker_fee: float = TAKER_FEE,
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
                        pnl = pcap * gain - pcap * (maker_fee + taker_fee)
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
                        pnl  = t["capital"] * gain - t["capital"] * (maker_fee + taker_fee)
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
            pnl  = t["capital"] * gain - t["capital"] * (maker_fee + taker_fee)
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
    maker_fee: float = MAKER_FEE,
    taker_fee: float = TAKER_FEE,
) -> list[dict]:
    """
    Cascade DCA entry: Slot 1 fires on the base signal.
    Each subsequent slot auto-enters when the previous slot is open AND price
    drops cascade_drop_pct below that slot's entry price.
    Each slot has its own trailing stop and hard stop — exits are independent.
    When a slot exits its position, the cascade trigger for the next slot is cleared.

    Slot capital defaults to an equal split, but can be front-loaded via
    params["slots"]["slot_capital_weight"] (e.g. [33, 25, 20, 12, 10]) — same
    convention as staggered mode. Weights are normalized to total_capital.

    Ladder stop (downside, optional via position.ladder_stop_buffer_pct): the
    moment the NEXT deeper slot fires, a shallower slot's trailing stop arms
    at (deeper slot's entry price) * (1 - buffer_pct) and ratchets up with the
    high-water mark from that point forward — bounding a shallow slot's max
    loss to roughly one cascade step instead of leaving it frozen forever or
    exposed to the full ladder depth. The deepest slot never gets one (no
    slot below it to arm off of).
    """
    position        = params.get("position", {})
    trail_pct       = position.get("trailing_stop_pct")
    trail_steps     = position.get("trailing_stop_steps")  # [[gain_pct, trail_pct], ...] ascending
    trail_arm_gain_pct = position.get("trail_arm_gain_pct")  # trail stays off until this much gain from entry
    stop_loss_pct   = position.get("stop_loss_pct")
    ladder_buffer_pct = position.get("ladder_stop_buffer_pct")
    cascade_drop    = position.get("cascade_drop_pct", 5.0) / 100.0
    expiry          = position.get("entry_expiry_candles", 2)
    min_hold        = position.get("min_hold_candles") or 0
    max_hold        = position.get("max_hold_candles")

    weights = (params.get("slots") or {}).get("slot_capital_weight")
    if weights and len(weights) >= slot_count:
        total_w = sum(weights[:slot_count])
        slot_capitals = [total_capital * w / total_w for w in weights[:slot_count]]
    else:
        slot_capitals = [total_capital / slot_count] * slot_count

    slots = [
        {
            "idx":              i,
            "slot_number":      i + 1,
            "open_trade":       None,
            "pending_entry":    None,
            "capital":          slot_capitals[i],
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
                    # arm the ladder stop on the previous (shallower) slot, if still open
                    if ladder_buffer_pct:
                        prev_idx = slot["idx"] - 1
                        if prev_idx >= 0 and slots[prev_idx]["open_trade"] is not None:
                            pt = slots[prev_idx]["open_trade"]
                            if not pt.get("ladder_armed"):
                                pt["ladder_armed"] = True
                                pt["ladder_peak"]  = lp
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

                gain_pct = (t["highest_close"] - t["entry_price"]) / t["entry_price"] * 100
                armed = (not trail_arm_gain_pct) or gain_pct >= trail_arm_gain_pct

                if trail_pct and armed:
                    eff_trail = trail_pct
                    if trail_steps:
                        for threshold, tighter in sorted(trail_steps, key=lambda x: x[0]):
                            if gain_pct >= threshold:
                                eff_trail = tighter
                    trail_stop = t["highest_close"] * (1 - eff_trail / 100.0)
                    if trail_arm_gain_pct:
                        # Once armed, never trail back below breakeven (entry + round-trip fee) —
                        # the arm threshold alone doesn't guarantee that if trail_pct is wide.
                        breakeven = t["entry_price"] * (1 + maker_fee + taker_fee)
                        trail_stop = max(trail_stop, breakeven)
                else:
                    trail_stop = None

                if t.get("ladder_armed"):
                    t["ladder_peak"] = max(t["ladder_peak"], row["close"])
                    ladder_stop = t["ladder_peak"] * (1 - ladder_buffer_pct / 100.0)
                else:
                    ladder_stop = None

                hard_stop  = t["entry_price"]   * (1 - stop_loss_pct / 100.0) if stop_loss_pct else None
                candidates = [s for s in [trail_stop, hard_stop, ladder_stop] if s is not None]
                if candidates:
                    stop_price = max(candidates)
                elif trail_arm_gain_pct:
                    stop_price = None  # not armed yet, no hard stop configured — hold, cascade can still add
                else:
                    stop_price = t["highest_close"] * 0.97  # legacy safety net when no stop is configured at all

                if t["candles_held"] >= min_hold:
                    exit_price = exit_reason = None
                    if max_hold and t["candles_held"] >= max_hold:
                        exit_price, exit_reason = row["close"], "max_hold"
                    elif stop_price is not None and row["low"] <= stop_price:
                        exit_price = stop_price
                        if hard_stop is not None and stop_price == hard_stop:
                            exit_reason = "stop_loss"
                        elif ladder_stop is not None and stop_price == ladder_stop:
                            exit_reason = "ladder_stop"
                        else:
                            exit_reason = "trailing_stop"

                    if exit_price:
                        gain = (exit_price - t["entry_price"]) / t["entry_price"]
                        pnl  = t["capital"] * gain - t["capital"] * (maker_fee + taker_fee)
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
            pnl      = t["capital"] * gain - t["capital"] * (maker_fee + taker_fee)
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


def _run_blended_slots(
    df: pd.DataFrame,
    signals: pd.Series,
    params: dict,
    slot_count: int,
    total_capital: float,
    maker_fee: float = MAKER_FEE,
    taker_fee: float = TAKER_FEE,
) -> list[dict]:
    """
    Blended-average DCA: ONE position, not N independent trades.

    Slot 1 fires on the base signal. Each subsequent add fires when price drops
    cumulative_drop_pcts[k] percent below slot 1's ORIGINAL entry (not the prior
    add) -- params.position["cumulative_drop_pcts"], e.g. [12, 25, 45, 70] for a
    5-slot ladder. Every fill updates one weighted-average cost basis (tracked in
    BTC-quantity terms so the average is a true dollar-cost-average, not a naive
    price average).

    There is no per-slot exit and no stop-loss: the whole stack exits together,
    once, off a single trailing stop computed from the blended average. The trail
    only arms once price is trail_arm_gain_pct above the average, and is floored
    at breakeven once armed -- so the position never voluntarily realizes a loss.
    The only way this shows a loss is a forced close at the end of the backtest
    window while still underwater and out of slots (same caveat as the
    independent no-stop cascade design).

    Optional capitulation ladder (capitulation_ladder_pcts + _final_cut_pct,
    mutually exclusive with capitulation_stop_pct -- if both set, the ladder
    wins). Once all slots are filled, each rung crossed below slot 1's original
    entry marks down ONE slot's capital (oldest first, no real sale -- shares
    and real cash deployed are untouched) to what it would be worth at that
    price. This only lowers the SYNTHETIC average used to gate the
    arm/breakeven exit, making a partial bounce enough to trigger a real sale
    instead of requiring a full recovery to the true average. One rung past
    the last slot being marked is a real, unconditional exit -- same backstop
    role capitulation_stop_pct plays, just reached after slot_count chances at
    a smaller bounce-triggered exit instead of a single line.

    Optional sentiment-tilted slot weighting (position["sentiment_tilt"], see
    tilted_slot_weights in slot_math.py), slot promotion for stagnant positions
    (slot_promotion_days / max_promotions_per_position), and a shallow
    breakeven margin (shallow_breakeven_margin_pct / shallow_slot_threshold)
    that converts an exact-breakeven exit into a small guaranteed gain.
    """
    position           = params.get("position", {})
    cumulative_drops   = position.get("cumulative_drop_pcts", [])  # % below slot1 entry, per add
    trail_pct          = position.get("trailing_stop_pct")
    trail_arm_gain_pct = position.get("trail_arm_gain_pct")
    expiry             = position.get("entry_expiry_candles", 2)

    # Raises the breakeven floor itself by a small guaranteed margin for shallow
    # positions -- leaves the 5% peak-trail completely untouched, so big winners
    # are unaffected. Converts the specific dead-zone case (arms, then reverses
    # before real profit locks in) from exactly $0 to a small guaranteed gain.
    shallow_breakeven_margin_pct = position.get("shallow_breakeven_margin_pct")
    shallow_slot_threshold = position.get("shallow_slot_threshold", 3)

    # "Impatience" promotion. cumulative_drops stays the normal trigger sequence,
    # unchanged in the normal/fast-moving case. slot_promotion_days (e.g.
    # [10, 20, 30, 40], same shape/indexing as cumulative_drops) gives each not-yet-
    # filled add a SECOND, easier trigger -- the PRIOR slot's normal (not promoted)
    # threshold -- that activates once the position has been open that many days
    # without selling and without that slot's own normal trigger firing. Slot 2's
    # promoted level is 0% (slot 1's own entry). Days are counted from slot 1's
    # fill, independently per slot -- not reset or chained off when an earlier
    # slot actually promotes or fills. capitulation_stop_pct/the ladder are
    # unaffected -- still measured off whatever price the last slot actually
    # filled at, promoted or not.
    slot_promotion_days = position.get("slot_promotion_days")
    max_promotions = position.get("max_promotions_per_position")  # None = unlimited

    capitulation_stop_pct = position.get("capitulation_stop_pct")  # only armed once ALL slots are filled --
                                                                     # the one backstop for a crash worse than
                                                                     # anything seen historically (out of ammo,
                                                                     # no more room to average down further)

    # Alternative to capitulation_stop_pct (see docstring below): once all slots
    # fill, progressively mark down the oldest slot's cost basis at each rung
    # crossed (capitulation_ladder_pcts, one entry per slot) instead of one
    # single-shot line, with a final unconditional cut past the last rung
    # (capitulation_ladder_final_cut_pct). Mutually exclusive with
    # capitulation_stop_pct -- if both are set, the ladder wins.
    ladder_pcts = position.get("capitulation_ladder_pcts")
    ladder_final_cut_pct = position.get("capitulation_ladder_final_cut_pct")
    ladder_enabled = bool(ladder_pcts and ladder_final_cut_pct)
    if ladder_enabled and len(ladder_pcts) != slot_count:
        raise ValueError(f"capitulation_ladder_pcts must have {slot_count} entries, got {len(ladder_pcts)}")

    weights = (params.get("slots") or {}).get("slot_capital_weight")
    base_weights = weights if weights and len(weights) >= slot_count else [1] * slot_count
    compound = position.get("compound", False)
    sentiment_tilt = position.get("sentiment_tilt")  # see slot_math.tilted_slot_weights; requires params["sentiment"]=True

    available_capital = total_capital  # grows/shrinks as positions close, if compound=True
    slot_capitals = slot_capitals_for(available_capital, weights, slot_count)  # this position's frozen split

    all_trades = []

    pending_entry = None       # (limit_price, ttl) for slot 1 only
    pending_add   = None       # (limit_price, ttl, next_idx) for a cascade add -- same
                                # limit-order-with-expiry simulation as slot 1, not an instant fill
    position_open = False
    original_entry_price = None
    fills = []                 # list of (price, capital, qty, ts) for the open position
    highest_close = None
    entry_ts = None
    candles_held = 0
    marked_count = 0           # ladder only: how many (oldest-first) slots have been marked down
    marked_capitals = []       # ladder only: parallel to fills, real capital until marked
    promotions_used = 0        # slot_promotion_days only: promoted fills used by this position
    ever_armed = False         # once true, stays true for the life of this position -- see armed
                                # gate below: freezes composition (no more cascade adds) and makes
                                # capitulation permanently unreachable (arming always wins)

    def total_qty():
        return sum(q for _, _, q, _ in fills)

    def total_deployed():
        return sum(c for _, c, _, _ in fills)

    def avg_entry_price():
        q = total_qty()
        return total_deployed() / q if q > 0 else None

    for i, (ts, row) in enumerate(df.iterrows()):

        # --- try to fill a pending slot-1 entry ---
        if pending_entry and not position_open:
            lp, ttl = pending_entry
            if row["low"] <= lp <= row["high"]:
                qty = (slot_capitals[0] * (1 - maker_fee)) / lp
                fills = [(lp, slot_capitals[0], qty, ts)]
                position_open = True
                original_entry_price = lp
                highest_close = lp
                entry_ts = ts
                candles_held = 0
                pending_entry = None
            else:
                ttl -= 1
                pending_entry = (lp, ttl) if ttl > 0 else None

        # --- try to fill a pending cascade add (same limit-order simulation as slot 1) ---
        if pending_add and position_open:
            lp, ttl, add_idx = pending_add
            if row["low"] <= lp <= row["high"]:
                qty = (slot_capitals[add_idx] * (1 - maker_fee)) / lp
                fills.append((lp, slot_capitals[add_idx], qty, ts))
                pending_add = None
            else:
                ttl -= 1
                pending_add = (lp, ttl, add_idx) if ttl > 0 else None

        # --- manage the open position ---
        if position_open:
            highest_close = max(highest_close, row["close"])
            candles_held += 1
            avg_ep = avg_entry_price()
            synthetic_avg = avg_ep

            # Ladder: once the stack is full, mark down one slot's capital (oldest
            # first) per step_pct rung crossed -- no real sale, just a lower
            # reference average that makes the arm/breakeven gate below easier to
            # clear on a partial bounce. Real deployed capital (used for actual P&L
            # at exit) is untouched by this.
            if ladder_enabled and len(fills) == slot_count:
                if not marked_capitals:
                    marked_capitals = [c for _, c, _, _ in fills]
                while marked_count < slot_count:
                    # rungs are measured below slot 1's original entry, not the last fill --
                    # e.g. slot 5 already sits 10% below slot 1, so a start_pct of 20 means
                    # "10% further than where slot 5 filled," not "20% past slot 5."
                    rung_price = original_entry_price * (1 - ladder_pcts[marked_count] / 100.0)
                    if row["low"] <= rung_price:
                        slot_price, slot_capital = fills[marked_count][0], fills[marked_count][1]
                        marked_capitals[marked_count] = slot_capital * (rung_price / slot_price)
                        marked_count += 1
                    else:
                        break
                synthetic_avg = sum(marked_capitals) / total_qty()

            gain_pct = (highest_close - synthetic_avg) / synthetic_avg * 100
            armed_now = (not trail_arm_gain_pct) or gain_pct >= trail_arm_gain_pct
            # Persisted once true, never reset (mathematically one-directional anyway --
            # HWM never falls and every new fill only lowers avg cost, which only
            # raises gain_pct). Arming does NOT stop cascade adds -- a resting buy
            # (next add, lower) and a resting sell (exit, higher) aren't in conflict,
            # and each new add only lowers the exit floor (more achievable), never
            # raises it. The one thing arming permanently disables is capitulation --
            # see the gate below.
            ever_armed = ever_armed or armed_now
            armed = ever_armed

            effective_trail_pct = trail_pct
            stop_price = None
            if effective_trail_pct and armed:
                stop_price = highest_close * (1 - effective_trail_pct / 100.0)
                if trail_arm_gain_pct:
                    # synthetic_avg already has the buy-side fee baked in (qty was reduced
                    # by (1-maker_fee) at each fill), so only the sell-side (taker, market
                    # order) fee needs pricing in here.
                    breakeven = synthetic_avg / (1 - taker_fee)
                    if shallow_breakeven_margin_pct and len(fills) <= shallow_slot_threshold:
                        breakeven *= (1 + shallow_breakeven_margin_pct / 100.0)
                    stop_price = max(stop_price, breakeven)

            # Capitulation backstop: only once every slot is filled (out of ammo -- no
            # more room to average down), a further drop forces a full exit instead of
            # holding indefinitely into the unknown. Ladder mode reaches this one step
            # past the last slot being marked; legacy mode is a single fixed line.
            # Permanently unreachable once armed -- a position that has proven it can
            # arm (a real profit floor exists) never gets forced into this deliberate
            # loss-taking backstop, which exists to protect positions that never did.
            capitulation_price = None
            if not armed:
                if ladder_enabled and len(fills) == slot_count and marked_count == slot_count:
                    capitulation_price = original_entry_price * (1 - ladder_final_cut_pct / 100.0)
                elif capitulation_stop_pct and len(fills) == slot_count:
                    last_fill_price = fills[-1][0]
                    capitulation_price = last_fill_price * (1 - capitulation_stop_pct / 100.0)

            exit_reason_override = None
            effective_stop = stop_price
            if capitulation_price is not None and row["low"] <= capitulation_price:
                if stop_price is None or capitulation_price < stop_price:
                    # only the capitulation stop is active (still underwater), or it's
                    # the more conservative of the two -- either way it's the one that fires
                    effective_stop = capitulation_price
                    exit_reason_override = "capitulation_ladder_cut" if ladder_enabled else "capitulation_stop"

            # Capitulation is a deliberate, forced cut -- modeled as a guaranteed
            # (market-style) fill, same as it is live, so only a low-touch is needed.
            # The armed/trailing-stop path is modeled as a real resting limit order
            # (see place_exit's live counterpart), so it needs the SAME two-sided
            # touch check every entry/add fill already uses below -- crediting a fill
            # at a price the candle's high never actually reached is exactly the bug
            # that let the backtest look far rosier than live replay ever could.
            is_capitulation = exit_reason_override is not None
            touched = (
                (effective_stop is not None and row["low"] <= effective_stop)
                if is_capitulation else
                (effective_stop is not None and row["low"] <= effective_stop <= row["high"])
            )

            if touched:
                exit_price = effective_stop
                gross = total_qty() * exit_price
                pnl   = gross * (1 - taker_fee) - total_deployed()
                all_trades.append({
                    "slot":            len(fills),   # num fills THIS position used, not a persistent slot id
                    "entry_ts":        entry_ts,
                    "exit_ts":         ts,
                    "entry_price":     avg_ep,        # blended, fee-adjusted average across all fills below
                    "exit_price":      exit_price,
                    "highest_close":   highest_close,
                    "capital":         total_deployed(),
                    "pnl":             pnl,
                    "exit_reason":     exit_reason_override or "trailing_stop",
                    "candles_held":    candles_held,
                    "fill_prices":     [p for p, _, _, _ in fills],
                    "fill_timestamps": [ft for _, _, _, ft in fills],
                    "fill_capitals":   [c for _, c, _, _ in fills],
                    "fill_qtys":       [q for _, _, q, _ in fills],
                    "marked_slots_used": marked_count if ladder_enabled else None,
                })
                position_open = False
                fills = []
                original_entry_price = None
                pending_add = None
                marked_count = 0
                marked_capitals = []
                promotions_used = 0
                ever_armed = False
                if compound:
                    available_capital += pnl
            else:
                # Cascade adds keep working the same whether armed or not -- a new,
                # cheaper fill only lowers the exit floor (avg cost drops), it never
                # raises it, so there's no conflict with an already-armed exit target.
                # Arm the next cascade add as a limit order (not an instant fill) --
                # it still has to actually get touched within `expiry` candles, same as slot 1
                next_idx = len(fills)  # number filled so far == index of the next add
                if next_idx < slot_count and (next_idx - 1) < len(cumulative_drops) and pending_add is None:
                    trigger_pct = cumulative_drops[next_idx - 1]
                    can_promote = max_promotions is None or promotions_used < max_promotions
                    if slot_promotion_days and can_promote and (next_idx - 1) < len(slot_promotion_days):
                        days_open = (ts - entry_ts).total_seconds() / 86400
                        if days_open >= slot_promotion_days[next_idx - 1]:
                            # prior slot's own normal threshold (0% for slot 2, promoting off slot 1)
                            trigger_pct = cumulative_drops[next_idx - 2] if next_idx >= 2 else 0.0
                            promotions_used += 1
                    trigger_price = original_entry_price * (1 - trigger_pct / 100.0)
                    if row["close"] <= trigger_price and slot_capitals[next_idx] > 0.01:
                        pending_add = (row["close"], expiry, next_idx)

        # --- slot 1: base signal entry ---
        if not position_open and pending_entry is None and signals.iloc[i]:
            if compound or sentiment_tilt:
                capital_base = available_capital if compound else total_capital
                if sentiment_tilt:
                    fng_value = row.get("fng_value")
                    if isinstance(fng_value, float) and pd.isna(fng_value):
                        fng_value = None
                    trend_period = sentiment_tilt.get("trend_sma_period")
                    trend_val = row.get(f"trend_sma_{trend_period}") if trend_period else None
                    if isinstance(trend_val, float) and pd.isna(trend_val):
                        trend_val = None
                    effective_weights = tilted_slot_weights(
                        base_weights, fng_value, sentiment_tilt, slot_count,
                        trend_val=trend_val, close=row["close"],
                    )
                else:
                    effective_weights = weights
                slot_capitals = slot_capitals_for(capital_base, effective_weights, slot_count)
            if slot_capitals[0] > 0.01:
                pending_entry = (row["close"], expiry)

    # close an open position at end of data
    if position_open:
        last_row  = df.iloc[-1]
        exit_price = last_row["close"]
        gross = total_qty() * exit_price
        pnl   = gross * (1 - taker_fee) - total_deployed()
        all_trades.append({
            "slot":            len(fills),
            "entry_ts":        entry_ts,
            "exit_ts":         df.index[-1],
            "entry_price":     avg_entry_price(),
            "exit_price":      exit_price,
            "highest_close":   highest_close,
            "capital":         total_deployed(),
            "pnl":             pnl,
            "exit_reason":     "end_of_data",
            "candles_held":    candles_held,
            "fill_prices":     [p for p, _, _, _ in fills],
            "fill_timestamps": [ft for _, _, _, ft in fills],
            "fill_capitals":   [c for _, c, _, _ in fills],
            "fill_qtys":       [q for _, _, q, _ in fills],
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
    maker_fee: float = MAKER_FEE,
    taker_fee: float = TAKER_FEE,
) -> dict:
    """
    Run a full backtest for a single stream configuration.

    slot_mode controls how multiple slots enter:
      'single'     — one slot, lot_size_usd capital
      'scale_down' — slot 2 adds when price drops X% below slot 1's entry (see slot2_trigger_pct in params.position)
      'scale_up'   — slot 2 adds when price rises X% above slot 1's entry AND signal fires

    maker_fee/taker_fee default to the real current Kraken rate (src/fees.py) --
    override to re-run history under a hypothetical rate without editing code.

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
        all_trades = _run_staggered_slots(df, signals, params, slot_count, lot_size_usd,
                                          maker_fee=maker_fee, taker_fee=taker_fee)
    elif slot_mode == 'cascade' and slot_count >= 2:
        all_trades = _run_cascade_slots(df, signals, params, slot_count, lot_size_usd,
                                        maker_fee=maker_fee, taker_fee=taker_fee)
    elif slot_mode == 'blended' and slot_count >= 2:
        all_trades = _run_blended_slots(df, signals, params, slot_count, lot_size_usd,
                                        maker_fee=maker_fee, taker_fee=taker_fee)
    else:
        slot1_trades = _run_slot(df, signals, params, slot=1, initial_capital=lot_size_usd,
                                 maker_fee=maker_fee, taker_fee=taker_fee)
        all_trades = slot1_trades
        if slot_count >= 2 and slot_mode in ('scale_down', 'scale_up'):
            slot2_signals = _derive_slot2_signals(df, slot1_trades, slot_mode, params, signals)
            slot2_trades  = _run_slot(df, slot2_signals, params, slot=2, initial_capital=lot_size_usd,
                                      maker_fee=maker_fee, taker_fee=taker_fee)
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
        "maker_fee":   maker_fee,
        "taker_fee":   taker_fee,
    }
