"""
Trailing stop monitor for open live positions.
Called once per candle close for each relevant timeframe.

For each OPEN lot belonging to a stream whose candle just closed:
  1. Update high_water_mark to max(current_hwm, candle_close)
  2. Compute stop_price = hwm * (1 - trail_pct)
  3. If candle_low <= stop_price: trigger market exit
"""
import logging

from sqlalchemy import text

from src.live import order_manager
from src.live.kraken_client import KrakenClient

log = logging.getLogger(__name__)


def check_all(
    conn,
    streams_by_id: dict,
    candle_row: dict,
    closed_timeframes: set,
    kraken: KrakenClient,
    dry_run: bool = False,
) -> int:
    """
    Check trailing stops for all OPEN lots whose stream's timeframe just closed.

    streams_by_id: {stream_id: stream_dict} — all live streams, keyed by stream_id
    candle_row: {stream_id: {'close': float, 'low': float}} — latest completed candle per stream
    closed_timeframes: set of timeframe strings that closed this tick (e.g. {'1h'} or {'1h', '4h'})
    """
    if not closed_timeframes:
        return 0

    open_lots = conn.execute(
        text("""
            SELECT lot_id, stream_id, entry_price, high_water_mark, btc_quantity, opening_capital
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
        trail_pct = stream["parameters"]["position"]["trailing_stop_pct"] / 100.0

        # Enforce min_hold if configured
        # (min_hold tracking would require candles_held count — deferred for v1;
        #  backtest showed min_hold rarely binding in practice)

        new_hwm = max(float(lot.high_water_mark), close)
        stop_price = new_hwm * (1 - trail_pct)

        # Always update HWM
        conn.execute(
            text("UPDATE live.lots SET high_water_mark = :hwm WHERE lot_id = :lid"),
            {"hwm": new_hwm, "lid": lot.lot_id},
        )

        if low <= stop_price:
            log.info(
                f"Trailing stop triggered — lot {lot.lot_id} ({stream['stream_name']}): "
                f"low={low:.2f} <= stop={stop_price:.2f} (hwm={new_hwm:.2f}, trail={trail_pct*100:.1f}%)"
            )
            order_manager.place_exit(conn, lot, stop_price, kraken, dry_run)
            stops_triggered += 1
        else:
            log.debug(
                f"Lot {lot.lot_id} ({stream['stream_name']}): "
                f"hwm={new_hwm:.2f} stop={stop_price:.2f} low={low:.2f} — holding"
            )

    return stops_triggered
