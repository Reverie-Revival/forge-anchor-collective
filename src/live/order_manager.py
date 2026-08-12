"""
Order lifecycle manager for live lots.
Handles the full state machine: CASH → PENDING → OPEN → CLOSED.

All DB writes use the passed connection (caller manages the transaction).
In dry_run mode, Kraken calls are skipped and logged instead.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.backtester.slot_math import slot_capitals_for
from src.fees import MAKER_FEE, TAKER_FEE
from src.live.kraken_client import KrakenClient
from src.live import notifier
from src.live import bucket_manager

log = logging.getLogger(__name__)


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


def get_reserve_state(conn, model_id: int):
    """Return this model's pooled capital reserve row (docs/decisions/008),
    or None if it hasn't opted in -- callers must fall back to plain fixed
    lot_size_usd sizing when this returns None. A model with no row here
    trades exactly as it always has; provisioning a row is a deliberate,
    separate deployment step."""
    return conn.execute(
        text("""
            SELECT baseline_total, pool_balance, hard_floor, halted_at
            FROM live.capital_reserve WHERE model_id = :mid
        """),
        {"mid": model_id},
    ).fetchone()


def _reserve_entry_capital(conn, stream: dict, base_capital: float = None) -> float:
    """Reserve-aware entry size: base_capital (the full configured
    lot_size_usd, or -- for a staggered slot -- that slot's own
    share of it, see _slot_capital_for) if this model has no pooled reserve
    (opt-in) or the pool is at/above baseline; shrunk proportionally to the
    slot's own weight of the model's baseline if the pool is below baseline;
    0.0 if even that shrunk share can't clear the $10 hard minimum --
    callers must treat 0.0 as "skip this entry"."""
    lot_size_usd = float(base_capital) if base_capital is not None else float(stream["lot_size_usd"])
    reserve = get_reserve_state(conn, stream["model_id"])
    if reserve is None:
        return lot_size_usd
    baseline_total, pool_balance, _, _ = reserve
    baseline_total = float(baseline_total)
    pool_balance = float(pool_balance)
    if pool_balance >= baseline_total:
        return lot_size_usd
    weight = lot_size_usd / baseline_total
    capital = pool_balance * weight
    return capital if capital >= 10.0 else 0.0


def _slot_capital_for(stream: dict, slot_number: int) -> float:
    """This slot's share of the stream's lot_size_usd, per
    params['slots']['slot_capital_weight'] -- same slot_capitals_for used by
    the backtester (src/backtester/slot_math.py), so a staggered
    stream's live capital split is provably the same formula that was
    backtested, not a hand-ported copy. Single-slot streams (slot_count < 2,
    the only mode with real capital today) get the full lot_size_usd
    unchanged -- this function is a no-op for them."""
    slot_count = int(stream.get("slot_count", 1) or 1)
    if slot_count < 2:
        return float(stream["lot_size_usd"])
    weights = stream["parameters"].get("slots", {}).get("slot_capital_weight")
    capitals = slot_capitals_for(float(stream["lot_size_usd"]), weights, slot_count)
    return capitals[slot_number - 1]


def _slot_free_priority(conn, stream_id: int, slot_count: int) -> dict:
    """For staggered dispatch: {slot_number: last_freed_at or None}. None
    (never occupied) sorts before any real timestamp -- matches engine.py's
    _run_staggered_slots, where a never-used slot's last_freed_candle=-1
    sorts first."""
    rows = conn.execute(
        text("""
            SELECT slot_number, MAX(closed_at) AS freed_at
            FROM live.lots
            WHERE stream_id = :sid AND status = 'CLOSED'
            GROUP BY slot_number
        """),
        {"sid": stream_id},
    ).fetchall()
    freed = {r.slot_number: r.freed_at for r in rows}
    return {s: freed.get(s) for s in range(1, slot_count + 1)}


def next_signal_slot(conn, stream: dict, now: datetime = None) -> int | None:
    """Which slot number a fresh signal should enter into, or None if no
    slot is eligible right now.

    single/scale_down/scale_up (only single has real live capital today):
    slot 1 only, exactly the old hardcoded behavior.
    staggered: dispatches to whichever eligible slot has been free longest
    (matches engine.py's _run_staggered_slots), gated by
    params['slots']['slot_entry_gap_candles'] measured from the most recent
    entry across ANY of this stream's slots.

    cascade live parity (price-trigger-driven adds off a prior slot's own
    fill) was built and then removed 2026-08-10 -- see docs/decisions/009 --
    after its live-replay trade count didn't match engine.py's and the
    mismatch wasn't run down. slot_mode='cascade' is not supported live.
    """
    slot_mode = stream.get("slot_mode", "single")
    slot_count = int(stream.get("slot_count", 1) or 1)
    stream_id = stream["stream_id"]

    if slot_mode != "staggered" or slot_count < 2:
        return 1 if slot_is_available(conn, stream_id, 1) else None

    if now is None:
        now = datetime.now(timezone.utc)

    available = [s for s in range(1, slot_count + 1) if slot_is_available(conn, stream_id, s)]
    if not available:
        return None

    gap_candles = stream["parameters"].get("slots", {}).get("slot_entry_gap_candles", 0)
    if gap_candles:
        tf = stream["parameters"].get("primary_timeframe", "1h")
        gap_minutes = _tf_minutes(tf) * gap_candles
        last_entry = conn.execute(
            text("SELECT MAX(opened_at) FROM live.lots WHERE stream_id = :sid"),
            {"sid": stream_id},
        ).scalar()
        if last_entry is not None:
            last_entry = last_entry if last_entry.tzinfo else last_entry.replace(tzinfo=timezone.utc)
            if (now - last_entry).total_seconds() < gap_minutes * 60:
                return None

    priorities = _slot_free_priority(conn, stream_id, slot_count)
    available.sort(key=lambda s: (priorities[s] is not None, priorities[s]))
    return available[0]


# Same tuned dynamic_skim values as docs/decisions/007's original design and
# src/backtester/model_engine.py's run_pooled_model_backtest -- kept as
# constants here rather than DB config, same pattern as MAKER_FEE/TAKER_FEE.
_SKIM_TARGET_TRADES = 22
_SKIM_AVG_WIN_PCT = 1.8
_SKIM_MIN_PCT = 10.0
_SKIM_MAX_PCT = 25.0


def _update_reserve(conn, model_id: int, pnl: float) -> None:
    """Apply a realized pnl to this model's pooled reserve, if it has one
    (opt-in, no-op otherwise). If this pnl pushes the pool above baseline,
    skims the surplus portion (docs/decisions/008: never the part that's
    still rebuilding the pool toward baseline) into this model's BTC bucket,
    if it has one (bucket_manager.add_skim is itself opt-in -- no-ops if
    there's a reserve but no bucket, surplus just stays as pool cash then).
    Detects and alerts the FIRST time the pool crosses its hard floor --
    this does not self-recover (see docs/decisions/008: once every stream's
    share is below $10, nothing is left trading to produce the win that
    would lift the pool back up), so it needs a real page, not a silent log
    line."""
    reserve = get_reserve_state(conn, model_id)
    if reserve is None:
        return
    baseline_total, pool_balance, hard_floor, halted_at = reserve
    baseline_total = float(baseline_total)
    pool_balance = float(pool_balance)
    hard_floor = float(hard_floor)
    new_balance = pool_balance + pnl

    if pnl > 0 and bucket_manager.get_bucket_state(conn, model_id) is not None:
        surplus_before = max(0.0, pool_balance - baseline_total)
        surplus_after = max(0.0, new_balance - baseline_total)
        new_surplus = surplus_after - surplus_before
        if new_surplus > 0:
            raw_pct = 10.0 / ((_SKIM_AVG_WIN_PCT / 100.0) * new_balance * _SKIM_TARGET_TRADES) * 100.0
            skim_pct = max(_SKIM_MIN_PCT, min(_SKIM_MAX_PCT, raw_pct))
            skim_amount = new_surplus * skim_pct / 100.0
            new_balance -= skim_amount
            bucket_manager.add_skim(conn, model_id, skim_amount)

    newly_halted = halted_at is None and new_balance < hard_floor
    now = datetime.now(timezone.utc)

    conn.execute(
        text("""
            UPDATE live.capital_reserve
            SET pool_balance = :bal,
                halted_at = CASE WHEN :newly_halted THEN :now ELSE halted_at END,
                updated_at = :now
            WHERE model_id = :mid
        """),
        {"bal": new_balance, "newly_halted": newly_halted, "now": now, "mid": model_id},
    )
    if newly_halted:
        log.error(f"Model {model_id} capital reserve HALTED at ${new_balance:.2f} (floor ${hard_floor:.2f})")
        notifier.alert_capital_halted(model_id, new_balance, hard_floor)


def place_entry(
    conn,
    stream: dict,
    kraken: KrakenClient,
    dry_run: bool = False,
    slot_number: int = 1,
    entry_reason: str = None,
) -> None:
    """
    Place a limit buy order and create a PENDING lot in the given slot_number
    (1 for single/scale/staggered's dispatched slot; 2+ only for a staggered
    dispatch -- see next_signal_slot, the only caller that should ever pass
    slot_number != 1).
    Uses the current Kraken ticker price as the limit price.

    Sizing is slot-aware (_slot_capital_for -- a no-op full lot_size_usd for
    single-slot streams) then reserve-aware (docs/decisions/008, opt-in via
    live.capital_reserve): normally that full slot capital, shrunk
    proportionally if this model has a pooled reserve below baseline, or
    skipped entirely (no order, no DB row -- caller's signal is simply not
    acted on this time) if even the shrunk share can't clear $10.
    """
    stream_id = stream["stream_id"]
    model_id = stream["model_id"]
    base_capital = _slot_capital_for(stream, slot_number)
    entry_capital = _reserve_entry_capital(conn, stream, base_capital)
    if entry_capital <= 0:
        log.info(f"{stream['stream_name']}: pooled reserve share below $10 minimum -- skipping entry")
        return
    params = stream["parameters"]
    tf = params.get("primary_timeframe", "1h")
    expiry_candles = params.get("position", {}).get("entry_expiry_candles", 2)

    limit_price = kraken.get_ticker_price() if not dry_run else 99999.99
    btc_qty = entry_capital / limit_price
    expiry_at = datetime.now(timezone.utc) + timedelta(minutes=_tf_minutes(tf) * expiry_candles)
    lot_seq = _next_lot_sequence(conn, stream_id)

    if dry_run:
        txid = f"DRY-{stream['stream_name']}-{lot_seq}"
        log.info(f"[DRY RUN] Would place limit buy: {stream['stream_name']} "
                 f"${entry_capital:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
    else:
        try:
            txid = kraken.place_order("buy", btc_qty, limit_price, "limit")
            log.info(f"Placed limit buy: {stream['stream_name']} "
                     f"${entry_capital:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
            expiry_str = expiry_at.strftime("%Y-%m-%d %H:%M UTC")
            notifier.alert_order_placed(stream["stream_name"], model_id, entry_capital,
                                        limit_price, btc_qty, expiry_str)
        except Exception as e:
            log.error(f"Failed to place entry order for {stream['stream_name']}: {e}")
            notifier.alert_order_failed(stream["stream_name"], model_id, entry_capital, str(e))
            return

    conn.execute(
        text("""
            INSERT INTO live.lots
                (model_id, stream_id, slot_number, lot_sequence, status,
                 opening_capital, btc_quantity, entry_price, entry_order_id,
                 entry_expiry_at, entry_reason, opened_at)
            VALUES
                (:mid, :sid, :slot, :seq, 'PENDING',
                 :capital, :qty, :price, :txid,
                 :expiry, :reason, :now)
        """),
        {
            "mid":     model_id,
            "sid":     stream_id,
            "slot":    slot_number,
            "seq":     lot_seq,
            "capital": entry_capital,
            "qty":     btc_qty,
            "price":   limit_price,
            "txid":    txid,
            "expiry":  expiry_at,
            "reason":  entry_reason or f"signal:{params.get('core_signal')}",
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

    _update_reserve(conn, model_id, pnl)
