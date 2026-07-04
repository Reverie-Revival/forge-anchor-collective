"""
Order lifecycle manager for live lots.
Handles the full state machine: CASH → PENDING → OPEN → CLOSED.

All DB writes use the passed connection (caller manages the transaction).
In dry_run mode, Kraken calls are skipped and logged instead.
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.live.kraken_client import KrakenClient

log = logging.getLogger(__name__)

MAKER_FEE = 0.0025   # 0.25% — limit entry
TAKER_FEE = 0.0040   # 0.40% — market exit


def _tf_minutes(tf: str) -> int:
    return {"15m": 15, "1h": 60, "4h": 240}.get(tf, 60)


def _next_lot_sequence(conn, stream_id: int) -> int:
    result = conn.execute(
        text("SELECT COALESCE(MAX(lot_sequence), 0) + 1 FROM live.lots WHERE stream_id = :sid"),
        {"sid": stream_id},
    )
    return result.scalar()


def slot_is_available(conn, stream_id: int, slot_number: int) -> bool:
    """Return True if the slot has no PENDING or OPEN lot."""
    result = conn.execute(
        text("""
            SELECT EXISTS (
                SELECT 1 FROM live.lots
                WHERE stream_id = :sid AND slot_number = :slot
                  AND status IN ('PENDING', 'OPEN')
            )
        """),
        {"sid": stream_id, "slot": slot_number},
    )
    return not result.scalar()


def place_entry(
    conn,
    stream: dict,
    kraken: KrakenClient,
    dry_run: bool = False,
) -> None:
    """
    Place a limit buy order and create a PENDING lot.
    Uses the current Kraken ticker price as the limit price.
    """
    stream_id = stream["stream_id"]
    model_id = stream["model_id"]
    lot_size_usd = float(stream["lot_size_usd"])
    params = stream["parameters"]
    tf = params.get("primary_timeframe", "1h")
    expiry_candles = params.get("position", {}).get("entry_expiry_candles", 2)

    limit_price = kraken.get_ticker_price() if not dry_run else 99999.99
    btc_qty = lot_size_usd / limit_price
    expiry_at = datetime.now(timezone.utc) + timedelta(minutes=_tf_minutes(tf) * expiry_candles)
    lot_seq = _next_lot_sequence(conn, stream_id)

    if dry_run:
        txid = f"DRY-{stream['stream_name']}-{lot_seq}"
        log.info(f"[DRY RUN] Would place limit buy: {stream['stream_name']} "
                 f"${lot_size_usd:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
    else:
        try:
            txid = kraken.place_order("buy", btc_qty, limit_price, "limit")
            log.info(f"Placed limit buy: {stream['stream_name']} "
                     f"${lot_size_usd:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
        except Exception as e:
            log.error(f"Failed to place entry order for {stream['stream_name']}: {e}")
            return

    conn.execute(
        text("""
            INSERT INTO live.lots
                (model_id, stream_id, slot_number, lot_sequence, status,
                 opening_capital, btc_quantity, entry_price, entry_order_id,
                 entry_expiry_at, entry_reason, opened_at)
            VALUES
                (:mid, :sid, 1, :seq, 'PENDING',
                 :capital, :qty, :price, :txid,
                 :expiry, :reason, :now)
        """),
        {
            "mid":     model_id,
            "sid":     stream_id,
            "seq":     lot_seq,
            "capital": lot_size_usd,
            "qty":     btc_qty,
            "price":   limit_price,
            "txid":    txid,
            "expiry":  expiry_at,
            "reason":  f"signal:{params.get('core_signal')}",
            "now":     datetime.now(timezone.utc),
        },
    )


def check_pending(conn, kraken: KrakenClient, dry_run: bool = False) -> tuple[int, int]:
    """
    Poll Kraken for all PENDING lots.
    Flip to OPEN on fill, or cancel + reset to CASH on expiry.
    Returns (fills, expirations).
    """
    now = datetime.now(timezone.utc)
    pending = conn.execute(
        text("""
            SELECT lot_id, stream_id, entry_order_id, entry_expiry_at, btc_quantity
            FROM live.lots WHERE status = 'PENDING'
        """)
    ).fetchall()

    fills = 0
    expirations = 0

    for lot in pending:
        if dry_run:
            log.debug(f"[DRY RUN] Skipping fill check for lot_id={lot.lot_id}")
            continue

        try:
            order = kraken.get_order_status(lot.entry_order_id)
        except Exception as e:
            log.error(f"Could not query order {lot.entry_order_id} for lot {lot.lot_id}: {e}")
            continue

        status = order.get("status", "")
        vol_exec = float(order.get("vol_exec", 0) or 0)

        if status == "closed" and vol_exec > 0:
            fill_price = float(order.get("price", 0) or 0)
            log.info(f"Lot {lot.lot_id} filled @ ${fill_price:.2f}")
            conn.execute(
                text("""
                    UPDATE live.lots
                    SET status = 'OPEN', entry_price = :price,
                        btc_quantity = :qty, high_water_mark = :price
                    WHERE lot_id = :lid
                """),
                {"price": fill_price, "qty": vol_exec, "lid": lot.lot_id},
            )
            fills += 1

        elif status in ("canceled", "expired") or (
            lot.entry_expiry_at and now > lot.entry_expiry_at.replace(tzinfo=timezone.utc)
        ):
            log.info(f"Lot {lot.lot_id} entry expired/cancelled — cancelling order {lot.entry_order_id}")
            try:
                kraken.cancel_order(lot.entry_order_id)
            except Exception as e:
                log.warning(f"Cancel attempt for {lot.entry_order_id} raised: {e}")
            conn.execute(
                text("DELETE FROM live.lots WHERE lot_id = :lid"),
                {"lid": lot.lot_id},
            )
            expirations += 1

    return fills, expirations


def place_exit(conn, lot, current_price: float, kraken: KrakenClient, dry_run: bool = False) -> None:
    """
    Place a market sell order to exit an OPEN lot and mark it CLOSED.
    lot: Row with lot_id, btc_quantity, entry_price, opening_capital
    current_price: the candle close price (used for P&L estimate in dry run)
    """
    if dry_run:
        exit_price = current_price
        log.info(f"[DRY RUN] Would market sell lot {lot.lot_id}: "
                 f"{lot.btc_quantity:.8f} BTC @ ~${exit_price:.2f}")
        txid = f"DRY-EXIT-{lot.lot_id}"
    else:
        try:
            txid = kraken.place_order("sell", float(lot.btc_quantity), order_type="market")
            exit_price = current_price  # actual fill price resolved on next check; use close as estimate
            log.info(f"Market sell placed for lot {lot.lot_id}: "
                     f"{lot.btc_quantity:.8f} BTC txid={txid}")
        except Exception as e:
            log.error(f"Failed to place exit order for lot {lot.lot_id}: {e}")
            return

    gain = (exit_price - float(lot.entry_price)) / float(lot.entry_price)
    capital = float(lot.opening_capital)
    pnl = capital * gain - capital * (MAKER_FEE + TAKER_FEE)

    conn.execute(
        text("""
            UPDATE live.lots
            SET status = 'CLOSED',
                exit_price = :price,
                exit_order_id = :txid,
                closing_capital = :closing,
                realized_pnl = :pnl,
                exit_reason = 'trailing_stop',
                closed_at = :now
            WHERE lot_id = :lid
        """),
        {
            "price":   exit_price,
            "txid":    txid,
            "closing": capital + pnl,
            "pnl":     round(pnl, 4),
            "now":     datetime.now(timezone.utc),
            "lid":     lot.lot_id,
        },
    )
