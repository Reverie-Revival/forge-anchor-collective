"""
Order lifecycle manager for live lots.
Handles the full state machine: CASH → PENDING → OPEN → CLOSED.

All DB writes use the passed connection (caller manages the transaction).
In dry_run mode, Kraken calls are skipped and logged instead.
"""
import logging
import time
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

# How hard place_exit tries to read back the real fill of a market sell before
# it settles for an estimate. Only the status READ is ever retried -- the sell
# itself is placed exactly once, so a retry can never double-sell.
EXIT_FILL_POLL_ATTEMPTS = 4
EXIT_FILL_POLL_DELAY_S = 2.0


def _confirmed_fill(order: dict) -> bool:
    return (order.get("status") == "closed"
            and float(order.get("vol_exec", 0) or 0) > 0
            and float(order.get("price", 0) or 0) > 0)


def _poll_exit_fill(kraken, txid: str, lot_id: int) -> dict:
    """Poll for a market sell's real fill, retrying briefly. Returns the
    confirmed order dict, or {} if it never confirmed (or Kraken kept
    erroring). 2026-11-12 lot 10: one immediate poll missed a real fill, the
    row was booked at the estimate, and the real loss was 14% larger."""
    for attempt in range(1, EXIT_FILL_POLL_ATTEMPTS + 1):
        try:
            order = kraken.get_order_status(txid)
        except Exception as e:
            log.warning(f"Exit order {txid} (lot {lot_id}) status poll {attempt}/"
                        f"{EXIT_FILL_POLL_ATTEMPTS} failed: {e}")
            order = {}
        if _confirmed_fill(order):
            return order
        if attempt < EXIT_FILL_POLL_ATTEMPTS:
            time.sleep(EXIT_FILL_POLL_DELAY_S)
    return {}


def _estimated_exit_price(kraken, fallback_price: float) -> float:
    """Best guess at a market sell's price when the real fill can't be read.
    The caller's price is the STOP level on a stop exit, but price gaps
    through the stop, so the real fill is below it -- using it always flatters
    the loss. The live ticker, a few seconds after the sell, is the closer
    estimate; fall back to the caller's price only if the ticker is down."""
    try:
        px = float(kraken.get_ticker_price())
        if px > 0:
            return px
    except Exception as e:
        log.warning(f"Ticker unavailable for exit-price estimate, using {fallback_price:.2f}: {e}")
    return fallback_price


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
            notifier.alert_order_failed(stream["stream_name"], model_id, lot_size_usd, str(e))
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


def place_exit(conn, lot, current_price: float, kraken: KrakenClient, dry_run: bool = False,
                stream_name: str = "", model_id: int = 0, exit_reason: str = "trailing_stop") -> None:
    """
    Place a market sell order to exit an OPEN lot and mark it CLOSED.
    lot: Row with lot_id, btc_quantity, entry_price, opening_capital, entry_fee_usd
    current_price: the candle close price (used for P&L estimate in dry run,
        and as the exit-price fallback if Kraken's post-placement status poll
        doesn't confirm a real fill yet)
    exit_reason: 'trailing_stop' (default) or 'max_hold' -- see position_monitor.check_all.

    Market sells fill essentially instantly on Kraken, so right after placing
    the order we poll get_order_status (a few short retries, read-only) for
    the real fill price and real fee. If it still isn't confirmed, fall back
    to an estimate (live ticker price, else current_price; fee from the
    TAKER_FEE constant) and flag the row with fee_is_estimated so it's
    visible for manual reconciliation, rather than blocking the lot from
    closing.
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

        order = _poll_exit_fill(kraken, txid, lot.lot_id)

        if order:
            exit_price = float(order.get("price", 0) or 0)
            exit_fee_usd = float(order.get("fee", 0) or 0)
        else:
            log.error(f"Exit order {txid} (lot {lot.lot_id}) not confirmed filled after "
                      f"{EXIT_FILL_POLL_ATTEMPTS} polls -- using estimated price/fee, "
                      "flagging for manual reconciliation (Kraken TradesHistory by ordertxid)")
            exit_price = _estimated_exit_price(kraken, current_price)
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
                exit_reason = :exit_reason,
                closed_at = :now
            WHERE lot_id = :lid
        """),
        {
            "price":       exit_price,
            "txid":        txid,
            "exit_fee":    round(exit_fee_usd, 4),
            "estimated":   fee_is_estimated,
            "closing":     capital + pnl,
            "pnl":         round(pnl, 4),
            "exit_reason": exit_reason,
            "now":         datetime.now(timezone.utc),
            "lid":         lot.lot_id,
        },
    )
    if not dry_run:
        notifier.alert_closed(stream_name, model_id, float(lot.entry_price), exit_price,
                              capital, round(capital + pnl, 2), round(pnl, 2))
