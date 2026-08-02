"""
Trailing stop + capitulation stop monitor for Model 3's OPEN blended position.
Called once per candle close for the stream's timeframe.

Mirrors the exit logic in backtester._run_blended_slots exactly:
  1. Update highest_close to max(current, candle_close)
  2. Trail only arms once gain above avg_cost_basis clears trail_arm_gain_pct,
     and is floored at breakeven once armed (position never voluntarily
     realizes a loss)
  3. Capitulation backstop only arms once every slot is filled (out of ammo);
     a further drop below the LAST fill's price forces a full exit
"""
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
            SELECT position_id, stream_id, model_id, avg_cost_basis, highest_close,
                   capitulation_armed
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
        position_params = stream["parameters"]["position"]
        trail_pct = position_params["trailing_stop_pct"]
        trail_arm_gain_pct = position_params.get("trail_arm_gain_pct")
        capitulation_stop_pct = position_params.get("capitulation_stop_pct")

        avg_ep = float(pos.avg_cost_basis)
        new_hwm = max(float(pos.highest_close), close)

        gain_pct = (new_hwm - avg_ep) / avg_ep * 100
        armed = (not trail_arm_gain_pct) or gain_pct >= trail_arm_gain_pct

        stop_price = None
        if trail_pct and armed:
            stop_price = new_hwm * (1 - trail_pct / 100.0)
            if trail_arm_gain_pct:
                # Must match place_exit's real pnl formula exactly
                # (gross * (1 - TAKER_FEE) - total_deployed) -- a real market
                # sell, not the backtester's single-fee-rate assumption -- or
                # the "never voluntarily realize a loss" floor can be pierced
                # by the maker/taker fee delta.
                breakeven = avg_ep / (1 - TAKER_FEE)
                stop_price = max(stop_price, breakeven)

        capitulation_price = None
        if capitulation_stop_pct and pos.capitulation_armed:
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
                exit_reason = "capitulation_stop"

        conn.execute(
            text("UPDATE live.blended_positions SET highest_close = :hwm WHERE position_id = :pid"),
            {"hwm": new_hwm, "pid": pos.position_id},
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
