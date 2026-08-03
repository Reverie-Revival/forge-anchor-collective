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
from src.live import notifier

log = logging.getLogger(__name__)

# Kraken's lowest volume tier (confirmed via TradeVolume API 2026-08-03,
# tiervolume=0, nextvolume=$2500/30d) -- both models' accounts start here.
# Fees step down as 30-day volume crosses $2,500 (taker -> 0.60% next tier).
# Re-check via `kraken._api.query_private('TradeVolume', {'pair': 'XXBTZUSD'})`
# if volume has grown, rather than assuming this is still current.
MAKER_FEE = 0.0040   # 0.40% — limit entry (real rate, was wrongly 0.25%)
TAKER_FEE = 0.0080   # 0.80% — market exit (real rate, was wrongly 0.40% --
                     # confirmed directly from Model 3's first real fill: $0.16
                     # fee on a $20.00005 trade = exactly 0.80%, and it was a
                     # taker fill despite being placed as a "limit" order)


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
            expiry_str = expiry_at.strftime("%Y-%m-%d %H:%M UTC")
            notifier.alert_order_placed(stream["stream_name"], model_id, lot_size_usd,
                                        limit_price, btc_qty, expiry_str)
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
            SELECT ll.lot_id, ll.stream_id, ll.model_id, ls.stream_name,
                   ll.entry_order_id, ll.entry_expiry_at, ll.btc_quantity,
                   ll.opening_capital, ll.entry_price
            FROM live.lots ll
            JOIN live.streams ls ON ls.stream_id = ll.stream_id
            WHERE ll.status = 'PENDING'
        """)
    ).fetchall()

    fills = 0
    expirations = 0

    for lot in pending:
        if dry_run:
            log.debug(f"[DRY RUN] Skipping fill check for lot_id={lot.lot_id}")
            continue

        expiry_ts = lot.entry_expiry_at.replace(tzinfo=timezone.utc) if lot.entry_expiry_at else None
        our_expiry_passed = expiry_ts and now > expiry_ts

        if our_expiry_passed:
            log.info(f"Lot {lot.lot_id} past expiry — cancelling order {lot.entry_order_id}")
            try:
                kraken.cancel_order(lot.entry_order_id)
            except Exception as e:
                log.warning(f"Cancel attempt for {lot.entry_order_id} raised: {e}")
            conn.execute(text("DELETE FROM live.lots WHERE lot_id = :lid"), {"lid": lot.lot_id})
            notifier.alert_order_expired(lot.stream_name, lot.model_id, float(lot.entry_price))
            expirations += 1
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
            entry_fee_usd = float(order.get("fee", 0) or 0)
            log.info(f"Lot {lot.lot_id} filled @ ${fill_price:.2f} (fee ${entry_fee_usd:.4f})")
            conn.execute(
                text("""
                    UPDATE live.lots
                    SET status = 'OPEN', entry_price = :price,
                        btc_quantity = :qty, high_water_mark = :price,
                        entry_fee_usd = :fee
                    WHERE lot_id = :lid
                """),
                {"price": fill_price, "qty": vol_exec, "fee": entry_fee_usd, "lid": lot.lot_id},
            )
            notifier.alert_opened(lot.stream_name, lot.model_id, float(lot.opening_capital), fill_price, vol_exec)
            fills += 1

        elif status in ("canceled", "expired"):
            log.info(f"Lot {lot.lot_id} cancelled/expired on Kraken — freeing slot")
            conn.execute(text("DELETE FROM live.lots WHERE lot_id = :lid"), {"lid": lot.lot_id})
            notifier.alert_order_expired(lot.stream_name, lot.model_id, float(lot.entry_price))
            expirations += 1

    return fills, expirations


def place_exit(conn, lot, current_price: float, kraken: KrakenClient, dry_run: bool = False, stream_name: str = "", model_id: int = 0) -> None:
    """
    Place a market sell order to exit an OPEN lot and mark it CLOSED.
    lot: Row with lot_id, btc_quantity, entry_price, opening_capital, entry_fee_usd
    current_price: the candle close price (used for P&L estimate in dry run,
        and as the exit-price fallback if Kraken's post-placement status poll
        doesn't confirm a real fill yet)

    Market sells fill essentially instantly on Kraken, so right after placing
    the order we poll get_order_status once for the real fill price and real
    fee. If that poll doesn't yet show a confirmed fill (rare -- API lag),
    fall back to the estimate (current_price + fee constants) and flag the
    row with fee_is_estimated so it's visible for a manual glance later,
    rather than blocking the lot from closing.
    """
    entry_fee_usd = float(lot.entry_fee_usd) if lot.entry_fee_usd is not None else None
    fee_is_estimated = False

    if dry_run:
        exit_price = current_price
        exit_fee_usd = float(lot.opening_capital) * TAKER_FEE
        log.info(f"[DRY RUN] Would market sell lot {lot.lot_id}: "
                 f"{lot.btc_quantity:.8f} BTC @ ~${exit_price:.2f}")
        txid = f"DRY-EXIT-{lot.lot_id}"
    else:
        try:
            txid = kraken.place_order("sell", float(lot.btc_quantity), order_type="market")
            log.info(f"Market sell placed for lot {lot.lot_id}: "
                     f"{lot.btc_quantity:.8f} BTC txid={txid}")
        except Exception as e:
            log.error(f"Failed to place exit order for lot {lot.lot_id}: {e}")
            return

        try:
            order = kraken.get_order_status(txid)
        except Exception as e:
            log.warning(f"Could not confirm fill for exit order {txid} (lot {lot.lot_id}): {e}")
            order = {}

        if order.get("status") == "closed" and float(order.get("vol_exec", 0) or 0) > 0:
            exit_price = float(order.get("price", 0) or 0)
            exit_fee_usd = float(order.get("fee", 0) or 0)
        else:
            log.warning(f"Exit order {txid} (lot {lot.lot_id}) not confirmed filled on first poll -- "
                        "using estimated price/fee, flagging for manual reconciliation")
            exit_price = current_price
            exit_fee_usd = float(lot.opening_capital) * TAKER_FEE
            fee_is_estimated = True

    if entry_fee_usd is None:
        entry_fee_usd = float(lot.opening_capital) * MAKER_FEE  # legacy lot, opened before this fix
        fee_is_estimated = True

    gain = (exit_price - float(lot.entry_price)) / float(lot.entry_price)
    capital = float(lot.opening_capital)
    pnl = capital * gain - entry_fee_usd - exit_fee_usd

    conn.execute(
        text("""
            UPDATE live.lots
            SET status = 'CLOSED',
                exit_price = :price,
                exit_order_id = :txid,
                exit_fee_usd = :exit_fee,
                fee_is_estimated = :estimated,
                closing_capital = :closing,
                realized_pnl = :pnl,
                exit_reason = 'trailing_stop',
                closed_at = :now
            WHERE lot_id = :lid
        """),
        {
            "price":     exit_price,
            "txid":      txid,
            "exit_fee":  round(exit_fee_usd, 4),
            "estimated": fee_is_estimated,
            "closing":   capital + pnl,
            "pnl":       round(pnl, 4),
            "now":       datetime.now(timezone.utc),
            "lid":       lot.lot_id,
        },
    )
    if not dry_run:
        notifier.alert_closed(stream_name, model_id, float(lot.entry_price), exit_price,
                              capital, round(capital + pnl, 2), round(pnl, 2))
