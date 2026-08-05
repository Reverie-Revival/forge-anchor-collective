"""
Trailing stop + capitulation stop monitor for an OPEN blended position.
Called once per candle close for the stream's timeframe.

Mirrors the exit logic in backtester._run_blended_slots exactly:
  1. Update highest_close to max(current, candle_close)
  2. Trail only arms once gain above the (possibly ladder-adjusted) average
     clears trail_arm_gain_pct -- persisted once true (trail_armed), never
     reset. Once armed, a REAL resting limit sell is placed/re-priced at the
     breakeven-floored trailing stop (see ensure_pending_exit/
     check_pending_exit in blended_order_manager.py) -- not an immediate
     market sell, which could (and did, during a live-replay audit) fill far
     below the intended floor during an active crash. Cascade adds keep
     working normally whether armed or not; a new, cheaper fill only lowers
     the floor, never raises it.
  3. Capitulation backstop only arms once every slot is filled (out of ammo)
     AND the position has never armed -- permanently unreachable once
     trail_armed (a position that's proven it can arm never gets forced into
     this deliberate loss-taking backstop). When it does fire, it's still an
     immediate market sell -- a genuine forced cut needs guaranteed
     execution, not a resting order. Legacy mode: a further drop below the
     LAST fill's price forces a full exit. Ladder mode (mutually exclusive,
     wins if both configured): progressively marks down the oldest slot's
     cost basis at each rung crossed below slot 1's original entry, with a
     real unconditional exit one rung past the last slot being marked.
"""
import json
import logging
from datetime import datetime, timezone

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
                   total_deployed, capitulation_armed, original_entry_price, marked_count,
                   marked_capitals, trail_armed, trail_armed_at, pending_exit_order_id, pending_exit_price
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
        armed_now = (not trail_arm_gain_pct) or gain_pct >= trail_arm_gain_pct
        # Persisted once true, never reset (mathematically one-directional --
        # HWM never falls and every new fill only lowers avg cost, which only
        # raises gain_pct further). Distinct from capitulation_armed ("all
        # slots filled"). Once trail_armed, capitulation is permanently
        # disabled for this position -- see the gate below -- but cascade
        # adds keep working exactly as before (a new, cheaper fill only
        # lowers the exit floor, never raises it, so there's no conflict).
        trail_armed = bool(pos.trail_armed) or bool(armed_now)
        trail_armed_at = pos.trail_armed_at if pos.trail_armed else (
            datetime.now(timezone.utc) if armed_now else None
        )

        stop_price = None
        if trail_pct and trail_armed:
            stop_price = new_hwm * (1 - trail_pct / 100.0)
            if trail_arm_gain_pct:
                # Necessarily an estimate, not a real-fee lookup -- this floor
                # is evaluated BEFORE the exit order exists, so there's no
                # real fee to read yet. Deliberately still TAKER_FEE-based
                # (not MAKER_FEE) so the "never voluntarily realize a loss"
                # floor holds against the real exit fee, which is normally
                # close to but not exactly this rate.
                breakeven = synthetic_avg / (1 - TAKER_FEE)
                if (shallow_breakeven_margin_pct and fill_count is not None
                        and fill_count <= shallow_slot_threshold):
                    breakeven *= (1 + shallow_breakeven_margin_pct / 100.0)
                stop_price = max(stop_price, breakeven)

        # Capitulation is permanently unreachable once armed -- a position
        # that's proven it can arm (a real profit floor exists) never gets
        # forced into this deliberate loss-taking backstop, which exists to
        # protect positions that never did. Stays a real market sell
        # (guaranteed, immediate) when it does fire -- same reasoning as
        # Model 1's hard stop-loss.
        capitulation_price = None
        if not trail_armed:
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

        exit_reason = "capitulation_ladder_cut" if (ladder_enabled and capitulation_price is not None) else (
            "capitulation_stop" if capitulation_price is not None else None
        )

        conn.execute(
            text("""
                UPDATE live.blended_positions
                SET highest_close = :hwm, marked_count = :mc, marked_capitals = CAST(:mcap AS jsonb),
                    trail_armed = :armed, trail_armed_at = :armed_at
                WHERE position_id = :pid
            """),
            {"hwm": new_hwm, "mc": marked_count,
             "mcap": None if marked_capitals is None else json.dumps(marked_capitals),
             "armed": trail_armed, "armed_at": trail_armed_at,
             "pid": pos.position_id},
        )

        full_pos = conn.execute(
            text("""
                SELECT position_id, total_qty, total_deployed, pending_exit_order_id, pending_exit_price
                FROM live.blended_positions WHERE position_id = :pid
            """),
            {"pid": pos.position_id},
        ).fetchone()

        if capitulation_price is not None and low <= capitulation_price:
            log.info(
                f"{exit_reason} triggered -- position {pos.position_id} ({stream['stream_name']}): "
                f"low={low:.2f} <= capitulation={capitulation_price:.2f}"
            )
            order_manager.place_exit(conn, full_pos, capitulation_price, exit_reason, kraken, dry_run,
                                     stream_name=stream["stream_name"], model_id=stream["model_id"])
            stops_triggered += 1
        elif trail_armed and stop_price is not None:
            # Ensure a resting real limit sell tracks the current floor --
            # Kraken's own order book decides if/when it actually fills;
            # check_pending_exit() polls for that fill separately each tick.
            # No "low <= stop_price" gate here: that was a backtest-only
            # simulation concept (approximating whether a resting order
            # would have been touched) -- live doesn't need to guess, the
            # real exchange handles it.
            order_manager.ensure_pending_exit(conn, full_pos, stop_price, kraken, dry_run)
        else:
            log.debug(
                f"Position {pos.position_id} ({stream['stream_name']}): "
                f"hwm={new_hwm:.2f} stop={stop_price} low={low:.2f} -- holding"
            )

    return stops_triggered
