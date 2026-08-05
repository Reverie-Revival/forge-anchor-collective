"""
Trailing stop + capitulation stop monitor for an OPEN blended position.
Called once per candle close for the stream's timeframe.

Mirrors the exit logic in backtester._run_blended_slots exactly:
  1. Update highest_close to max(current, candle_close)
  2. Trail only arms once gain above the (possibly ladder-adjusted) average
     clears trail_arm_gain_pct, and is floored at breakeven once armed
     (position never voluntarily realizes a loss) -- optionally with a
     shallow_breakeven_margin_pct guaranteed margin for shallow positions
  3. Capitulation backstop only arms once every slot is filled (out of ammo).
     Legacy mode: a further drop below the LAST fill's price forces a full
     exit. Ladder mode (mutually exclusive, wins if both configured):
     progressively marks down the oldest slot's cost basis at each rung
     crossed below slot 1's original entry, with a real unconditional exit
     one rung past the last slot being marked.
"""
import json
import logging

from sqlalchemy import text

from src.live import blended_order_manager as order_manager
from src.live.kraken_client import KrakenClient
from src.live.order_manager import TAKER_FEE

log = logging.getLogger(__name__)


def check_all(
    conn,
    streams_by_id: dict,
    candle_row: dict,
    closed_timeframes: set,
    kraken: KrakenClient,
    dry_run: bool = False,
) -> int:
    if not closed_timeframes:
        return 0

    open_positions = conn.execute(
        text("""
            SELECT position_id, stream_id, model_id, avg_cost_basis, highest_close, total_qty,
                   capitulation_armed, original_entry_price, marked_count, marked_capitals
            FROM live.blended_positions
            WHERE status = 'OPEN'
        """)
    ).fetchall()

    if not open_positions:
        return 0

    stops_triggered = 0

    for pos in open_positions:
        stream = streams_by_id.get(pos.stream_id)
        if stream is None:
            log.warning(f"Position {pos.position_id} references unknown stream_id={pos.stream_id}")
            continue

        tf = stream["parameters"].get("primary_timeframe", "4h")
        if tf not in closed_timeframes:
            continue

        candle = candle_row.get(pos.stream_id)
        if candle is None:
            log.warning(f"No candle data for stream_id={pos.stream_id}, skipping position {pos.position_id}")
            continue

        close = candle["close"]
        low = candle["low"]
        slot_count = stream["slot_count"]
        position_params = stream["parameters"]["position"]
        trail_pct = position_params["trailing_stop_pct"]
        trail_arm_gain_pct = position_params.get("trail_arm_gain_pct")
        capitulation_stop_pct = position_params.get("capitulation_stop_pct")
        ladder_pcts = position_params.get("capitulation_ladder_pcts")
        ladder_final_cut_pct = position_params.get("capitulation_ladder_final_cut_pct")
        ladder_enabled = bool(ladder_pcts and ladder_final_cut_pct)
        shallow_breakeven_margin_pct = position_params.get("shallow_breakeven_margin_pct")
        shallow_slot_threshold = position_params.get("shallow_slot_threshold", 3)

        avg_ep = float(pos.avg_cost_basis)
        new_hwm = max(float(pos.highest_close), close)
        synthetic_avg = avg_ep
        marked_count = pos.marked_count or 0
        marked_capitals = pos.marked_capitals

        fill_rows = None
        if ladder_enabled or shallow_breakeven_margin_pct:
            # Only needed for the two Model-4-only mechanisms -- Model 3's
            # plain positions never pay this extra query.
            fill_rows = conn.execute(
                text("""
                    SELECT fill_number, price, capital FROM live.blended_fills
                    WHERE position_id = :pid ORDER BY fill_number
                """),
                {"pid": pos.position_id},
            ).fetchall()
        fill_count = len(fill_rows) if fill_rows is not None else None

        if ladder_enabled and fill_count == slot_count:
            if marked_capitals is None:
                marked_capitals = [float(f.capital) for f in fill_rows]
            while marked_count < slot_count:
                # Rungs measured below slot 1's ORIGINAL entry, not the last
                # fill -- same convention as engine.py's ladder.
                rung_price = float(pos.original_entry_price) * (1 - ladder_pcts[marked_count] / 100.0)
                if low <= rung_price:
                    slot_price = float(fill_rows[marked_count].price)
                    slot_capital = float(fill_rows[marked_count].capital)
                    marked_capitals[marked_count] = slot_capital * (rung_price / slot_price)
                    marked_count += 1
                else:
                    break
            synthetic_avg = sum(marked_capitals) / float(pos.total_qty)

        gain_pct = (new_hwm - synthetic_avg) / synthetic_avg * 100
        armed = (not trail_arm_gain_pct) or gain_pct >= trail_arm_gain_pct

        stop_price = None
        if trail_pct and armed:
            stop_price = new_hwm * (1 - trail_pct / 100.0)
            if trail_arm_gain_pct:
                # Necessarily an estimate, not a real-fee lookup -- this floor
                # is evaluated BEFORE the exit order exists, so there's no
                # real fee to read yet. Deliberately still TAKER_FEE-based
                # (not MAKER_FEE) so the "never voluntarily realize a loss"
                # floor holds against place_exit's real market-sell fee,
                # which is normally close to but not exactly this rate.
                breakeven = synthetic_avg / (1 - TAKER_FEE)
                if (shallow_breakeven_margin_pct and fill_count is not None
                        and fill_count <= shallow_slot_threshold):
                    breakeven *= (1 + shallow_breakeven_margin_pct / 100.0)
                stop_price = max(stop_price, breakeven)

        capitulation_price = None
        if ladder_enabled and fill_count == slot_count and marked_count == slot_count:
            capitulation_price = float(pos.original_entry_price) * (1 - ladder_final_cut_pct / 100.0)
        elif capitulation_stop_pct and pos.capitulation_armed:
            last_fill = conn.execute(
                text("""
                    SELECT price FROM live.blended_fills
                    WHERE position_id = :pid ORDER BY fill_number DESC LIMIT 1
                """),
                {"pid": pos.position_id},
            ).fetchone()
            if last_fill:
                capitulation_price = float(last_fill.price) * (1 - capitulation_stop_pct / 100.0)

        exit_reason = None
        effective_stop = stop_price
        if capitulation_price is not None and low <= capitulation_price:
            if stop_price is None or capitulation_price < stop_price:
                effective_stop = capitulation_price
                exit_reason = "capitulation_ladder_cut" if ladder_enabled else "capitulation_stop"

        conn.execute(
            text("""
                UPDATE live.blended_positions
                SET highest_close = :hwm, marked_count = :mc, marked_capitals = CAST(:mcap AS jsonb)
                WHERE position_id = :pid
            """),
            {"hwm": new_hwm, "mc": marked_count,
             "mcap": None if marked_capitals is None else json.dumps(marked_capitals),
             "pid": pos.position_id},
        )

        if effective_stop is not None and low <= effective_stop:
            reason = exit_reason or "trailing_stop"
            log.info(
                f"{reason} triggered -- position {pos.position_id} ({stream['stream_name']}): "
                f"low={low:.2f} <= stop={effective_stop:.2f} (hwm={new_hwm:.2f})"
            )
            full_pos = conn.execute(
                text("""
                    SELECT position_id, total_qty, total_deployed
                    FROM live.blended_positions WHERE position_id = :pid
                """),
                {"pid": pos.position_id},
            ).fetchone()
            order_manager.place_exit(conn, full_pos, effective_stop, reason, kraken, dry_run,
                                     stream_name=stream["stream_name"], model_id=stream["model_id"])
            stops_triggered += 1
        else:
            log.debug(
                f"Position {pos.position_id} ({stream['stream_name']}): "
                f"hwm={new_hwm:.2f} stop={effective_stop} low={low:.2f} -- holding"
            )

    return stops_triggered
