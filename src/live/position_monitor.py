"""
Trailing stop monitor for open live positions.
Called once per candle close for each relevant timeframe.

For each OPEN lot belonging to a stream whose candle just closed:
  1. Update high_water_mark to max(current_hwm, candle_close)
  2. If min_hold_candles hasn't elapsed yet, hold regardless of price (HWM
     still updates) -- matches src/backtester/engine.py _run_slot's
     "if candles_held >= min_hold: check exit conditions" gate.
  3. If max_hold_candles has elapsed, force a close at the current candle's
     close, unconditionally -- matches engine.py's max_hold branch. This one
     was missing entirely until 2026-08-07 (see HANDOFF.md): backtests for
     Dip Hunter (max_hold_candles=240) credited ~40% of trades to this exit,
     but live had no code path for it at all, so those positions just ran on
     using the trailing stop alone -- real money running a different rule
     set than the one that was backtested and approved.
  4. Otherwise: compute stop_price = hwm * (1 - trail_pct) -- trail_pct
     tightens as gain grows if trailing_stop_steps is set, and the trail
     doesn't arm at all until trail_arm_gain_pct is reached, if configured
     (also missing from live until this session, see the inline comment
     below) -- if candle_low <= stop_price, trigger a market exit.

candles_held is derived from wall-clock elapsed time since lot.opened_at,
not a stored counter -- there is no live equivalent of the backtest's
per-candle loop to increment one. This assumes check_all is only ever
called on this stream's own timeframe boundary (true today: executor.py
only calls it when the stream's tf is in closed_timeframes), so elapsed
whole periods lines up with the backtest's per-candle count.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from src.fees import MAKER_FEE, TAKER_FEE
from src.live import order_manager
from src.live.kraken_client import KrakenClient

log = logging.getLogger(__name__)

_TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240}


def check_all(
    conn,
    streams_by_id: dict,
    candle_row: dict,
    closed_timeframes: set,
    kraken: KrakenClient,
    now: datetime = None,
    dry_run: bool = False,
) -> int:
    """
    Check trailing stops (and min/max hold) for all OPEN lots whose stream's
    timeframe just closed.

    streams_by_id: {stream_id: stream_dict} — all live streams, keyed by stream_id
    candle_row: {stream_id: {'close': float, 'low': float}} — latest completed candle per stream
    closed_timeframes: set of timeframe strings that closed this tick (e.g. {'1h'} or {'1h', '4h'})
    now: current tick timestamp, used to derive candles_held from lot.opened_at.
        Defaults to real UTC now -- callers running a replay/backtest harness
        must pass the simulated tick timestamp explicitly, same pattern
        order_manager.place_entry's expiry calc and replay_gauntlet.py's
        _FakeDT already use elsewhere in this codebase.
    """
    if not closed_timeframes:
        return 0

    if now is None:
        now = datetime.now(timezone.utc)

    open_lots = conn.execute(
        text("""
            SELECT lot_id, stream_id, slot_number, entry_price, high_water_mark, btc_quantity,
                   opening_capital, entry_fee_usd, opened_at
            FROM live.lots
            WHERE status = 'OPEN'
        """)
    ).fetchall()

    if not open_lots:
        return 0

    stops_triggered = 0

    for lot in open_lots:
        stream = streams_by_id.get(lot.stream_id)
        if stream is None:
            log.warning(f"Lot {lot.lot_id} references unknown stream_id={lot.stream_id}")
            continue

        tf = stream["parameters"].get("primary_timeframe", "1h")
        if tf not in closed_timeframes:
            continue  # This stream's candle hasn't closed yet this tick

        candle = candle_row.get(lot.stream_id)
        if candle is None:
            log.warning(f"No candle data for stream_id={lot.stream_id}, skipping lot {lot.lot_id}")
            continue

        close = candle["close"]
        low = candle["low"]
        position_cfg = stream["parameters"]["position"]
        base_trail_pct = position_cfg.get("trailing_stop_pct")
        trail_steps = position_cfg.get("trailing_stop_steps")       # [[gain_pct, tighter_pct], ...]
        trail_arm_gain_pct = position_cfg.get("trail_arm_gain_pct")  # trail off until this much gain
        min_hold = position_cfg.get("min_hold_candles") or 0
        max_hold = position_cfg.get("max_hold_candles")
        stop_loss_pct = position_cfg.get("stop_loss_pct")

        opened_at = lot.opened_at.replace(tzinfo=timezone.utc) if lot.opened_at.tzinfo is None else lot.opened_at
        tf_minutes = _TF_MINUTES.get(tf, 60)
        candles_held = int((now - opened_at).total_seconds() // (tf_minutes * 60))

        new_hwm = max(float(lot.high_water_mark), close)

        # Always update HWM, even during min_hold -- matches engine.py, which
        # tracks highest_close unconditionally every candle regardless of the
        # min_hold gate on exit checks below.
        conn.execute(
            text("UPDATE live.lots SET high_water_mark = :hwm WHERE lot_id = :lid"),
            {"hwm": new_hwm, "lid": lot.lot_id},
        )

        if candles_held < min_hold:
            log.debug(f"Lot {lot.lot_id} ({stream['stream_name']}): "
                      f"candles_held={candles_held} < min_hold={min_hold} — holding regardless of price")
            continue

        if max_hold and candles_held >= max_hold:
            log.info(f"Max hold reached — lot {lot.lot_id} ({stream['stream_name']}): "
                     f"candles_held={candles_held} >= max_hold={max_hold}, forcing close @ {close:.2f}")
            order_manager.place_exit(conn, lot, close, kraken, dry_run,
                                     stream_name=stream["stream_name"], model_id=stream["model_id"],
                                     exit_reason="max_hold")
            stops_triggered += 1
            continue

        entry_price = float(lot.entry_price)
        gain_pct = (new_hwm - entry_price) / entry_price * 100
        armed = (not trail_arm_gain_pct) or gain_pct >= trail_arm_gain_pct

        # Trailing stop, with two engine.py mechanics that were missing
        # entirely from live before this session (found while validating
        # staggered/cascade live parity -- Cascade DCA v2's locked config
        # sets trailing_stop_steps, and the mismatch it caused against the
        # backtest reference in a replay run is what surfaced this gap; the
        # gap itself is general, not cascade-specific):
        #   trail_arm_gain_pct -- no trailing stop at all until price has
        #     moved this far in the position's favor; once armed, never
        #     trails back below breakeven (entry + round-trip fee).
        #   trailing_stop_steps -- [[gain_pct, tighter_trail_pct], ...]
        #     ascending: tighten the effective trail as gain grows, instead
        #     of one fixed pct for the whole hold.
        trail_stop = None
        if base_trail_pct and armed:
            eff_trail_pct = base_trail_pct
            if trail_steps:
                for threshold, tighter in sorted(trail_steps, key=lambda x: x[0]):
                    if gain_pct >= threshold:
                        eff_trail_pct = tighter
            trail_stop = new_hwm * (1 - eff_trail_pct / 100.0)
            if trail_arm_gain_pct:
                breakeven = entry_price * (1 + MAKER_FEE + TAKER_FEE)
                trail_stop = max(trail_stop, breakeven)

        # Hard stop loss, measured from entry and never moving -- distinct from
        # the trailing stop, which moves up with the high-water mark. Matches
        # engine.py: stop_price is whichever of the two is MORE protective
        # (higher/closer to current price), not the trailing stop alone.
        # This was entirely missing from live until 2026-08-07 -- Breakout
        # Scout v3 and Dip Hunter v3 both set stop_loss_pct.
        hard_stop = entry_price * (1 - stop_loss_pct / 100.0) if stop_loss_pct else None

        candidates = [s for s in (trail_stop, hard_stop) if s is not None]
        if candidates:
            stop_price = max(candidates)
        elif trail_arm_gain_pct:
            log.debug(f"Lot {lot.lot_id} ({stream['stream_name']}): trail not yet armed "
                      f"(gain={gain_pct:.2f}% < arm={trail_arm_gain_pct}%), no other stop configured — holding")
            continue
        else:
            # Legacy safety net matching engine.py -- only reachable if a
            # stream configures no stop mechanism at all, which no current
            # locked stream does.
            stop_price = new_hwm * 0.97

        if low <= stop_price:
            if hard_stop is not None and stop_price <= hard_stop:
                exit_reason = "stop_loss"
            else:
                exit_reason = "trailing_stop"
            log.info(
                f"{exit_reason} triggered — lot {lot.lot_id} ({stream['stream_name']}): "
                f"low={low:.2f} <= stop={stop_price:.2f} (hwm={new_hwm:.2f}, gain={gain_pct:.2f}%, "
                f"hard_stop={hard_stop})"
            )
            order_manager.place_exit(conn, lot, stop_price, kraken, dry_run,
                                     stream_name=stream["stream_name"], model_id=stream["model_id"],
                                     exit_reason=exit_reason)
            stops_triggered += 1
        else:
            log.debug(
                f"Lot {lot.lot_id} ({stream['stream_name']}): "
                f"hwm={new_hwm:.2f} stop={stop_price:.2f} low={low:.2f} — holding"
            )

    return stops_triggered
