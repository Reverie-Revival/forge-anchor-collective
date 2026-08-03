"""
Order lifecycle manager for Model 3's blended (Grid Stacker) position.

Unlike order_manager.py (independent lots, CASH -> PENDING -> OPEN -> CLOSED),
this manages ONE stack per stream: PENDING_ENTRY -> OPEN (-> more fills as
cascade adds trigger) -> CLOSED. Ported incrementally from the backtester's
_run_blended_slots (src/backtester/engine.py) -- same math, but fills are
confirmed by polling real Kraken order status instead of a candle-touch
simulation, and everything advances one executor tick at a time instead of
looping over a whole DataFrame at once.

Position sizing always reads live.blended_capital.available_capital -- NEVER
Kraken's actual account balance -- so this model can only ever spend money it
has itself realized, and can never draw on capital that belongs to Model 1
in the same Kraken account.

Fee model -- every entry (slot 1 and every cascade add) is a maker limit buy;
every exit is a taker market sell. Order volume = capital / limit_price
(Kraken bills the fee separately, it doesn't reduce vol_exec). Real per-trade
fees (not the MAKER_FEE/TAKER_FEE constants) now drive every P&L calc: each
fill's real `fee` from Kraken is captured in live.blended_fills.fee_usd at
entry/add time, summed and combined with the real exit fee in place_exit's
pnl calc. MAKER_FEE/TAKER_FEE only remain as narrow fallbacks -- for legacy
fills predating this fix (NULL fee_usd) and for the rare case where the
post-exit status poll doesn't confirm a fill yet (see place_exit). The
pre-trade breakeven floor in blended_position_monitor.py is a deliberate
exception -- it's evaluated before the exit order exists, so it MUST stay
TAKER_FEE-based (not MAKER_FEE) so "never voluntarily realize a loss" holds
against the real exit mechanism -- a mismatch there previously let a position
close at a small real loss; caught by tests/live/test_blended_state_machine.py,
now fixed and covered.

All DB writes use the passed connection (caller manages the transaction).
In dry_run mode, Kraken calls are skipped and logged instead.
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.live.kraken_client import KrakenClient
from src.live.order_manager import MAKER_FEE, TAKER_FEE, _tf_minutes
from src.backtester.slot_math import slot_capitals_for as _slot_capitals_for
from src.live import blended_notifier as notifier

log = logging.getLogger(__name__)


def get_available_capital(conn, model_id: int) -> float:
    row = conn.execute(
        text("SELECT available_capital FROM live.blended_capital WHERE model_id = :mid"),
        {"mid": model_id},
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"No live.blended_capital row for model_id={model_id}. "
            "Run deploy_model3.py first to seed the capital ledger."
        )
    return float(row.available_capital)


def _update_available_capital(conn, model_id: int, new_value: float) -> None:
    conn.execute(
        text("""
            UPDATE live.blended_capital
            SET available_capital = :val, updated_at = :now
            WHERE model_id = :mid
        """),
        {"val": round(new_value, 2), "mid": model_id, "now": datetime.now(timezone.utc)},
    )


def has_active_position(conn, stream_id: int) -> bool:
    """True if a PENDING_ENTRY or OPEN position already exists for this stream.

    Model 3 is a solo stream using the model's full capital -- only one
    stack can be building or open at a time.
    """
    result = conn.execute(
        text("""
            SELECT EXISTS (
                SELECT 1 FROM live.blended_positions
                WHERE stream_id = :sid AND status IN ('PENDING_ENTRY', 'OPEN')
            )
        """),
        {"sid": stream_id},
    )
    return bool(result.scalar())


def place_entry(conn, stream: dict, kraken: KrakenClient, dry_run: bool = False) -> None:
    """Place slot-1's limit buy and create a PENDING_ENTRY position."""
    stream_id = stream["stream_id"]
    model_id = stream["model_id"]
    params = stream["parameters"]
    tf = params.get("primary_timeframe", "4h")
    expiry_candles = params.get("position", {}).get("entry_expiry_candles", 2)
    slot_count = stream["slot_count"]
    weights = (params.get("slots") or {}).get("slot_capital_weight")

    capital_base = get_available_capital(conn, model_id)
    slot_capitals = _slot_capitals_for(capital_base, weights, slot_count)
    slot1_capital = slot_capitals[0]

    if slot1_capital < 10.0:
        log.warning(
            f"{stream['stream_name']}: slot-1 capital ${slot1_capital:.2f} below the $10 "
            "minimum lot size -- skipping entry this tick."
        )
        return

    limit_price = kraken.get_ticker_price() if not dry_run else 99999.99
    btc_qty = slot1_capital / limit_price
    expiry_at = datetime.now(timezone.utc) + timedelta(minutes=_tf_minutes(tf) * expiry_candles)

    if dry_run:
        txid = f"DRY-{stream['stream_name']}-entry"
        log.info(f"[DRY RUN] Would place limit buy (slot 1): {stream['stream_name']} "
                 f"${slot1_capital:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
    else:
        try:
            txid = kraken.place_order("buy", btc_qty, limit_price, "limit")
            log.info(f"Placed limit buy (slot 1): {stream['stream_name']} "
                     f"${slot1_capital:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
            expiry_str = expiry_at.strftime("%Y-%m-%d %H:%M UTC")
            notifier.alert_blend_order_placed(stream["stream_name"], model_id, 0,
                                              slot1_capital, limit_price, btc_qty, expiry_str)
        except Exception as e:
            log.error(f"Failed to place slot-1 entry for {stream['stream_name']}: {e}")
            return

    conn.execute(
        text("""
            INSERT INTO live.blended_positions
                (model_id, stream_id, status, position_capital_base,
                 pending_entry_order_id, pending_entry_expiry_at, created_at)
            VALUES
                (:mid, :sid, 'PENDING_ENTRY', :capital_base,
                 :txid, :expiry, :now)
        """),
        {
            "mid": model_id, "sid": stream_id, "capital_base": capital_base,
            "txid": txid, "expiry": expiry_at, "now": datetime.now(timezone.utc),
        },
    )


def _resolve_order(kraken: KrakenClient, order_id: str, expiry_ts, now, log_label: str):
    """
    Poll an order's status, cancelling it first if our own expiry has passed.

    Cancelling doesn't undo a fill that landed right before it took effect
    (including a partial fill) -- so this always requeries AFTER cancelling
    rather than trusting the cancel call alone, and callers must never
    discard a position based on vol_exec without looking at this result.

    Returns (order_dict, our_expiry_passed). order_dict is None if the status
    query itself failed -- callers should skip this position for this tick.
    """
    our_expiry_passed = bool(expiry_ts and now > expiry_ts)

    if our_expiry_passed:
        log.info(f"{log_label} past expiry -- cancelling {order_id}")
        try:
            kraken.cancel_order(order_id)
        except Exception as e:
            log.warning(f"Cancel attempt for {order_id} raised: {e}")

    try:
        order = kraken.get_order_status(order_id)
    except Exception as e:
        verb = "confirm final status of" if our_expiry_passed else "query"
        log.error(f"Could not {verb} {order_id} for {log_label}: {e}")
        return None, our_expiry_passed

    return order, our_expiry_passed


def check_pending_entry(conn, kraken: KrakenClient, streams: dict, dry_run: bool = False) -> tuple[int, int]:
    """
    Poll Kraken for PENDING_ENTRY positions. Flip to OPEN on fill, or delete on expiry.

    streams: {stream_id: stream_dict} -- the same dict the caller's tick()
    already loaded once this tick, reused here instead of re-querying
    live.streams for every fill.
    """
    now = datetime.now(timezone.utc)
    pending = conn.execute(
        text("""
            SELECT bp.position_id, bp.stream_id, bp.model_id, ls.stream_name,
                   bp.pending_entry_order_id, bp.pending_entry_expiry_at, bp.position_capital_base
            FROM live.blended_positions bp
            JOIN live.streams ls ON ls.stream_id = bp.stream_id
            WHERE bp.status = 'PENDING_ENTRY'
        """)
    ).fetchall()

    fills = 0
    expirations = 0

    for pos in pending:
        if dry_run:
            log.debug(f"[DRY RUN] Skipping fill check for position_id={pos.position_id}")
            continue

        expiry_ts = pos.pending_entry_expiry_at.replace(tzinfo=timezone.utc) if pos.pending_entry_expiry_at else None
        order, our_expiry_passed = _resolve_order(
            kraken, pos.pending_entry_order_id, expiry_ts, now, f"position {pos.position_id} slot-1"
        )
        if order is None:
            continue

        status = order.get("status", "")
        vol_exec = float(order.get("vol_exec", 0) or 0)

        if status == "open":
            # Still resting on the book. A nonzero vol_exec here is a
            # partial fill that could still grow with a LATER fill -- do
            # NOT finalize it yet, that would silently orphan the rest of
            # the order. If our expiry already passed, the cancel above
            # should have moved it out of "open"; if it's still "open"
            # anyway (cancel didn't take effect -- race/API hiccup), don't
            # touch anything and let the next tick retry the cancel.
            if our_expiry_passed:
                log.warning(f"Position {pos.position_id} order still 'open' after a cancel attempt "
                            f"(vol_exec={vol_exec:.8f}) -- will retry the cancel next tick")
            continue

        if vol_exec > 0:
            # Terminal state (closed = fully filled, or canceled/expired
            # with a partial fill locked in) -- safe to finalize now, this
            # volume will never change again.
            stream = streams.get(pos.stream_id)
            if stream is None:
                log.error(f"Position {pos.position_id}: stream_id={pos.stream_id} not in loaded streams, "
                          "cannot apply fill this tick")
                continue
            _apply_entry_fill(conn, pos, order, vol_exec, now, stream)
            fills += 1
        elif our_expiry_passed or status in ("canceled", "expired"):
            log.info(f"Position {pos.position_id} slot-1 order cancelled/expired on Kraken, zero fill -- freeing")
            conn.execute(text("DELETE FROM live.blended_positions WHERE position_id = :pid"), {"pid": pos.position_id})
            notifier.alert_blend_order_expired(pos.stream_name, pos.model_id, 0)
            expirations += 1

    return fills, expirations


def _apply_entry_fill(conn, pos, order: dict, vol_exec: float, now, stream: dict) -> None:
    """Record slot 1's fill (full or partial -- either way it's real BTC bought) and open the position."""
    fill_price = float(order.get("price", 0) or 0)
    fee_usd = float(order.get("fee", 0) or 0)
    weights = (stream["parameters"].get("slots") or {}).get("slot_capital_weight")
    slot_capitals = _slot_capitals_for(float(pos.position_capital_base), weights, stream["slot_count"])
    slot1_capital = slot_capitals[0]

    log.info(f"Position {pos.position_id} slot 1 filled @ ${fill_price:.2f} "
             f"(vol_exec={vol_exec:.8f}, fee ${fee_usd:.4f})")
    conn.execute(
        text("""
            INSERT INTO live.blended_fills
                (position_id, fill_number, price, capital, qty, order_id, fee_usd, filled_at)
            VALUES (:pid, 0, :price, :capital, :qty, :oid, :fee, :now)
        """),
        {"pid": pos.position_id, "price": fill_price, "capital": slot1_capital,
         "qty": vol_exec, "oid": pos.pending_entry_order_id, "fee": fee_usd, "now": now},
    )
    conn.execute(
        text("""
            UPDATE live.blended_positions
            SET status = 'OPEN', original_entry_price = :price,
                avg_cost_basis = :price, total_qty = :qty, total_deployed = :capital,
                highest_close = :price, pending_entry_order_id = NULL,
                pending_entry_expiry_at = NULL, opened_at = :now
            WHERE position_id = :pid
        """),
        {"price": fill_price, "qty": vol_exec, "capital": slot1_capital,
         "now": now, "pid": pos.position_id},
    )
    notifier.alert_blend_opened(pos.stream_name, pos.model_id, slot1_capital, fill_price, vol_exec)


def check_cascade_add_trigger(conn, stream: dict, latest_close: float, kraken: KrakenClient, dry_run: bool = False) -> None:
    """
    For the OPEN position on this stream (if any), check whether price has
    dropped far enough below slot 1's original entry to arm the next
    cascade add. Only one add order is ever in flight at a time -- mirrors
    the backtester's `pending_add is None` gate.
    """
    stream_id = stream["stream_id"]
    model_id = stream["model_id"]
    params = stream["parameters"]
    cumulative_drops = params.get("position", {}).get("cumulative_drop_pcts", [])
    slot_count = stream["slot_count"]
    weights = (params.get("slots") or {}).get("slot_capital_weight")
    expiry_candles = params.get("position", {}).get("entry_expiry_candles", 2)
    tf = params.get("primary_timeframe", "4h")

    pos = conn.execute(
        text("""
            SELECT position_id, original_entry_price, position_capital_base,
                   pending_add_order_id
            FROM live.blended_positions
            WHERE stream_id = :sid AND status = 'OPEN'
        """),
        {"sid": stream_id},
    ).fetchone()
    if pos is None or pos.pending_add_order_id is not None:
        return

    fill_count = conn.execute(
        text("SELECT COUNT(*) FROM live.blended_fills WHERE position_id = :pid"),
        {"pid": pos.position_id},
    ).scalar()

    next_idx = fill_count  # number filled so far == index of the next add
    if next_idx >= slot_count or (next_idx - 1) >= len(cumulative_drops):
        return  # out of slots or out of ladder config -- capitulation backstop owns this now

    slot_capitals = _slot_capitals_for(float(pos.position_capital_base), weights, slot_count)
    add_capital = slot_capitals[next_idx]
    if add_capital < 0.01:
        return

    trigger_price = float(pos.original_entry_price) * (1 - cumulative_drops[next_idx - 1] / 100.0)
    if latest_close > trigger_price:
        return

    expiry_at = datetime.now(timezone.utc) + timedelta(minutes=_tf_minutes(tf) * expiry_candles)
    limit_price = kraken.get_ticker_price() if not dry_run else latest_close
    btc_qty = add_capital / limit_price

    if dry_run:
        txid = f"DRY-{stream['stream_name']}-add{next_idx}"
        log.info(f"[DRY RUN] Would place cascade add #{next_idx}: {stream['stream_name']} "
                 f"${add_capital:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
    else:
        try:
            txid = kraken.place_order("buy", btc_qty, limit_price, "limit")
            log.info(f"Placed cascade add #{next_idx}: {stream['stream_name']} "
                     f"${add_capital:.2f} @ ${limit_price:.2f} ({btc_qty:.8f} BTC) txid={txid}")
            expiry_str = expiry_at.strftime("%Y-%m-%d %H:%M UTC")
            notifier.alert_blend_order_placed(stream["stream_name"], model_id, next_idx,
                                              add_capital, limit_price, btc_qty, expiry_str)
        except Exception as e:
            log.error(f"Failed to place cascade add #{next_idx} for {stream['stream_name']}: {e}")
            return

    conn.execute(
        text("""
            UPDATE live.blended_positions
            SET pending_add_order_id = :txid, pending_add_index = :idx,
                pending_add_expiry_at = :expiry
            WHERE position_id = :pid
        """),
        {"txid": txid, "idx": next_idx, "expiry": expiry_at, "pid": pos.position_id},
    )


def check_pending_add(conn, kraken: KrakenClient, streams: dict, dry_run: bool = False) -> tuple[int, int]:
    """
    Poll Kraken for in-flight cascade adds. Fold into the position on fill, or clear on expiry.

    streams: {stream_id: stream_dict} -- reused from the caller's tick(),
    same reasoning as check_pending_entry.
    """
    now = datetime.now(timezone.utc)
    pending = conn.execute(
        text("""
            SELECT bp.position_id, bp.stream_id, bp.model_id, ls.stream_name,
                   bp.pending_add_order_id, bp.pending_add_index, bp.pending_add_expiry_at,
                   bp.total_qty, bp.total_deployed, bp.position_capital_base
            FROM live.blended_positions bp
            JOIN live.streams ls ON ls.stream_id = bp.stream_id
            WHERE bp.status = 'OPEN' AND bp.pending_add_order_id IS NOT NULL
        """)
    ).fetchall()

    fills = 0
    expirations = 0

    for pos in pending:
        if dry_run:
            log.debug(f"[DRY RUN] Skipping add-fill check for position_id={pos.position_id}")
            continue

        expiry_ts = pos.pending_add_expiry_at.replace(tzinfo=timezone.utc) if pos.pending_add_expiry_at else None
        order, our_expiry_passed = _resolve_order(
            kraken, pos.pending_add_order_id, expiry_ts, now,
            f"position {pos.position_id} add #{pos.pending_add_index}"
        )
        if order is None:
            continue

        status = order.get("status", "")
        vol_exec = float(order.get("vol_exec", 0) or 0)

        if status == "open":
            # Still resting on the book -- see check_pending_entry's identical
            # comment. A partial vol_exec here could still grow; don't
            # finalize it, and don't touch anything if our own expiry timer
            # already fired but the cancel hasn't taken effect yet.
            if our_expiry_passed:
                log.warning(f"Position {pos.position_id} add order still 'open' after a cancel attempt "
                            f"(vol_exec={vol_exec:.8f}) -- will retry the cancel next tick")
            continue

        if vol_exec > 0:
            stream = streams.get(pos.stream_id)
            if stream is None:
                log.error(f"Position {pos.position_id}: stream_id={pos.stream_id} not in loaded streams, "
                          "cannot apply fill this tick")
                continue
            _apply_add_fill(conn, pos, order, vol_exec, now, stream)
            fills += 1
        elif our_expiry_passed or status in ("canceled", "expired"):
            log.info(f"Position {pos.position_id} add order cancelled/expired on Kraken, zero fill")
            conn.execute(
                text("""
                    UPDATE live.blended_positions
                    SET pending_add_order_id = NULL, pending_add_index = NULL, pending_add_expiry_at = NULL
                    WHERE position_id = :pid
                """),
                {"pid": pos.position_id},
            )
            notifier.alert_blend_order_expired(pos.stream_name, pos.model_id, pos.pending_add_index)
            expirations += 1

    return fills, expirations


def _apply_add_fill(conn, pos, order: dict, vol_exec: float, now, stream: dict) -> None:
    """Record a cascade add's fill (full or partial) and fold it into the blended average."""
    fill_price = float(order.get("price", 0) or 0)
    fee_usd = float(order.get("fee", 0) or 0)
    weights = (stream["parameters"].get("slots") or {}).get("slot_capital_weight")
    slot_count = stream["slot_count"]
    slot_capitals = _slot_capitals_for(float(pos.position_capital_base), weights, slot_count)
    add_capital = slot_capitals[pos.pending_add_index]

    new_qty = float(pos.total_qty) + vol_exec
    new_deployed = float(pos.total_deployed) + add_capital
    new_avg = new_deployed / new_qty

    log.info(f"Position {pos.position_id} add #{pos.pending_add_index} filled @ ${fill_price:.2f} "
             f"(vol_exec={vol_exec:.8f}, fee ${fee_usd:.4f}) -- new avg cost ${new_avg:.2f}")
    conn.execute(
        text("""
            INSERT INTO live.blended_fills
                (position_id, fill_number, price, capital, qty, order_id, fee_usd, filled_at)
            VALUES (:pid, :fnum, :price, :capital, :qty, :oid, :fee, :now)
        """),
        {"pid": pos.position_id, "fnum": pos.pending_add_index, "price": fill_price,
         "capital": add_capital, "qty": vol_exec, "oid": pos.pending_add_order_id,
         "fee": fee_usd, "now": now},
    )
    capitulation_armed = (pos.pending_add_index + 1) == slot_count
    conn.execute(
        text("""
            UPDATE live.blended_positions
            SET total_qty = :qty, total_deployed = :deployed, avg_cost_basis = :avg,
                pending_add_order_id = NULL, pending_add_index = NULL, pending_add_expiry_at = NULL,
                capitulation_armed = :armed
            WHERE position_id = :pid
        """),
        {"qty": new_qty, "deployed": new_deployed, "avg": new_avg,
         "armed": capitulation_armed, "pid": pos.position_id},
    )
    notifier.alert_blend_add_filled(pos.stream_name, pos.model_id, pos.pending_add_index + 1,
                                    add_capital, fill_price, new_avg)


def place_exit(conn, position, exit_price: float, exit_reason: str,
              kraken: KrakenClient, dry_run: bool = False,
              stream_name: str = "", model_id: int = 0) -> None:
    """
    Market-sell the whole blended stack and close the position.

    exit_price: the computed stop-trigger price -- used as-is in dry run, and
    as the fallback if Kraken's post-placement status poll doesn't confirm a
    real fill yet (market sells fill essentially instantly, so this is rare).
    Real fee is summed from live.blended_fills.fee_usd (every entry + cascade
    add) plus the real exit fee, replacing the old TAKER_FEE-only estimate.
    """
    total_qty = float(position.total_qty)
    total_deployed = float(position.total_deployed)
    fee_is_estimated = False

    # Per-fill, not a single SUM(fee_usd) -- a position can have a MIX of
    # legacy fills (NULL fee_usd, opened before this fix) and real ones (e.g.
    # slot 1 filled pre-migration, a later cascade add filled after). SQL SUM
    # silently ignores NULLs, which would undercount the legacy fill's fee
    # entirely and leave fee_is_estimated False -- wrong on both counts.
    fills = conn.execute(
        text("SELECT capital, fee_usd FROM live.blended_fills WHERE position_id = :pid"),
        {"pid": position.position_id},
    ).fetchall()
    total_entry_fees_usd = 0.0
    for fill in fills:
        if fill.fee_usd is not None:
            total_entry_fees_usd += float(fill.fee_usd)
        else:
            total_entry_fees_usd += float(fill.capital) * MAKER_FEE  # legacy fill, opened before this fix
            fee_is_estimated = True

    if dry_run:
        log.info(f"[DRY RUN] Would market sell position {position.position_id}: "
                 f"{total_qty:.8f} BTC @ ~${exit_price:.2f} ({exit_reason})")
        txid = f"DRY-EXIT-{position.position_id}"
        real_exit_price = exit_price
        exit_fee_usd = total_qty * exit_price * TAKER_FEE
    else:
        try:
            txid = kraken.place_order("sell", total_qty, order_type="market")
            log.info(f"Market sell placed for position {position.position_id}: "
                     f"{total_qty:.8f} BTC txid={txid} ({exit_reason})")
        except Exception as e:
            log.error(f"Failed to place exit order for position {position.position_id}: {e}")
            return

        try:
            order = kraken.get_order_status(txid)
        except Exception as e:
            log.warning(f"Could not confirm fill for exit order {txid} (position {position.position_id}): {e}")
            order = {}

        if order.get("status") == "closed" and float(order.get("vol_exec", 0) or 0) > 0:
            real_exit_price = float(order.get("price", 0) or 0)
            exit_fee_usd = float(order.get("fee", 0) or 0)
        else:
            log.warning(f"Exit order {txid} (position {position.position_id}) not confirmed filled on "
                        "first poll -- using estimated price/fee, flagging for manual reconciliation")
            real_exit_price = exit_price
            exit_fee_usd = total_qty * exit_price * TAKER_FEE
            fee_is_estimated = True

    pnl = (total_qty * real_exit_price) - exit_fee_usd - total_deployed - total_entry_fees_usd
    closing_capital = total_deployed + pnl

    conn.execute(
        text("""
            UPDATE live.blended_positions
            SET status = 'CLOSED', exit_price = :price, exit_order_id = :txid,
                exit_fee_usd = :exit_fee, fee_is_estimated = :estimated,
                closing_capital = :closing, realized_pnl = :pnl,
                exit_reason = :reason, closed_at = :now
            WHERE position_id = :pid
        """),
        {"price": real_exit_price, "txid": txid, "exit_fee": round(exit_fee_usd, 4),
         "estimated": fee_is_estimated, "closing": round(closing_capital, 2),
         "pnl": round(pnl, 2), "reason": exit_reason, "now": datetime.now(timezone.utc),
         "pid": position.position_id},
    )

    available_capital = get_available_capital(conn, model_id)
    _update_available_capital(conn, model_id, available_capital + pnl)

    if not dry_run:
        notifier.alert_blend_closed(stream_name, model_id, total_deployed,
                                    round(closing_capital, 2), round(pnl, 2), exit_reason)
