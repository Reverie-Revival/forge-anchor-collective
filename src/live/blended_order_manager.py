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
import json
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.live.kraken_client import KrakenClient
from src.live.order_manager import MAKER_FEE, TAKER_FEE, _tf_minutes
from src.backtester.slot_math import slot_capitals_for as _slot_capitals_for
from src.backtester.slot_math import tilted_slot_weights as _tilted_slot_weights
from src.live import blended_notifier as notifier

log = logging.getLogger(__name__)


def _get_fng_value(conn, on_date=None):
    """
    Most recent Fear & Greed reading on or before on_date (default: today) --
    same "on or before" tolerance as the backtester's day-keyed lookup, since
    alternative.me's daily update can lag past midnight UTC. Returns None if
    sentiment_data has no row that old yet (e.g. a brand-new deploy on a day
    the updater hasn't run), which callers treat as "no tilt this entry" --
    matching engine.py's fng_value=None short-circuit exactly.
    """
    row = conn.execute(
        text("""
            SELECT fng_value FROM sentiment_data
            WHERE date <= COALESCE(:on_date, CURRENT_DATE)
            ORDER BY date DESC LIMIT 1
        """),
        {"on_date": on_date},
    ).fetchone()
    return int(row.fng_value) if row is not None else None


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
    """Place slot-1's limit buy and create a PENDING_ENTRY position.

    If sentiment_tilt is configured, the tilted split is computed HERE, ONCE,
    using today's Fear & Greed reading, and frozen into frozen_slot_capitals
    on the position row -- cascade adds must reuse this exact split, never
    recompute the tilt against a later (different) fng_value. Mirrors
    engine.py's _run_blended_slots, which locks the tilt in at slot-1 entry
    for the same reason.
    """
    stream_id = stream["stream_id"]
    model_id = stream["model_id"]
    params = stream["parameters"]
    tf = params.get("primary_timeframe", "4h")
    position_params = params.get("position", {})
    expiry_candles = position_params.get("entry_expiry_candles", 2)
    slot_count = stream["slot_count"]
    weights = (params.get("slots") or {}).get("slot_capital_weight")
    sentiment_tilt = position_params.get("sentiment_tilt")

    capital_base = get_available_capital(conn, model_id)

    if sentiment_tilt:
        # tilted_slot_weights indexes base_weights directly -- needs the same
        # even-split fallback engine.py applies before calling it (plain
        # _slot_capitals_for has its own equivalent fallback built in, but
        # this function doesn't).
        base_weights = weights if weights and len(weights) >= slot_count else [1] * slot_count
        fng_value = _get_fng_value(conn)
        # trend_sma_period is a documented but never-adopted refinement (no
        # locked config uses it) -- not wired to a live SMA lookup. Passing
        # trend_val=None here is the same as it being unset: tilted_slot_weights
        # skips the trend adjustment entirely and falls back to plain strength.
        effective_weights = _tilted_slot_weights(base_weights, fng_value, sentiment_tilt, slot_count)
    else:
        effective_weights = weights

    slot_capitals = _slot_capitals_for(capital_base, effective_weights, slot_count)
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
                 frozen_slot_capitals, pending_entry_order_id,
                 pending_entry_expiry_at, created_at)
            VALUES
                (:mid, :sid, 'PENDING_ENTRY', :capital_base,
                 CAST(:slot_capitals AS jsonb), :txid, :expiry, :now)
        """),
        {
            "mid": model_id, "sid": stream_id, "capital_base": capital_base,
            "slot_capitals": json.dumps(slot_capitals),
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
                   bp.pending_entry_order_id, bp.pending_entry_expiry_at, bp.position_capital_base,
                   bp.frozen_slot_capitals
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
    # Read the split frozen at place_entry() time, not a fresh recompute --
    # if sentiment_tilt is configured, a fresh call would apply TODAY's
    # fng_value instead of the one this position actually opened under.
    # NULL only for positions opened before this column existed.
    if pos.frozen_slot_capitals is not None:
        slot_capitals = pos.frozen_slot_capitals
    else:
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

    Also implements slot_promotion_days's "impatience" trigger, exactly as
    coded in engine.py's _run_blended_slots (not just as its docstring
    describes it): once the position has been open long enough without this
    add's normal trigger firing, the PRIOR slot's easier threshold applies
    -- but promotions_used increments every tick this branch is evaluated
    while under max_promotions, not just when the promoted trigger actually
    fires. With max_promotions_per_position=1 (GS: Reflex v2's config), that
    means the promotion is only actually live for the one candle where the
    days threshold is first crossed -- if price doesn't touch it that candle,
    promotions_used is already spent and the trigger reverts to normal for
    the rest of the position's life. This is the real, tested, Gauntlet-
    passed behavior -- reproduced deliberately, not "fixed," for exact
    parity with what was backtested.
    """
    stream_id = stream["stream_id"]
    model_id = stream["model_id"]
    params = stream["parameters"]
    position_params = params.get("position", {})
    cumulative_drops = position_params.get("cumulative_drop_pcts", [])
    slot_promotion_days = position_params.get("slot_promotion_days")
    max_promotions = position_params.get("max_promotions_per_position")
    slot_count = stream["slot_count"]
    expiry_candles = position_params.get("entry_expiry_candles", 2)
    tf = params.get("primary_timeframe", "4h")

    pos = conn.execute(
        text("""
            SELECT position_id, original_entry_price, position_capital_base,
                   pending_add_order_id, frozen_slot_capitals, promotions_used, opened_at
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

    if pos.frozen_slot_capitals is not None:
        slot_capitals = pos.frozen_slot_capitals
    else:
        weights = (params.get("slots") or {}).get("slot_capital_weight")
        slot_capitals = _slot_capitals_for(float(pos.position_capital_base), weights, slot_count)
    add_capital = slot_capitals[next_idx]
    if add_capital < 0.01:
        return

    trigger_pct = cumulative_drops[next_idx - 1]
    promotions_used = pos.promotions_used
    can_promote = max_promotions is None or promotions_used < max_promotions
    if slot_promotion_days and can_promote and (next_idx - 1) < len(slot_promotion_days):
        opened_at = pos.opened_at.replace(tzinfo=timezone.utc) if pos.opened_at.tzinfo is None else pos.opened_at
        days_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 86400
        if days_open >= slot_promotion_days[next_idx - 1]:
            trigger_pct = cumulative_drops[next_idx - 2] if next_idx >= 2 else 0.0
            promotions_used += 1
            conn.execute(
                text("UPDATE live.blended_positions SET promotions_used = :p WHERE position_id = :pid"),
                {"p": promotions_used, "pid": pos.position_id},
            )

    trigger_price = float(pos.original_entry_price) * (1 - trigger_pct / 100.0)
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
                   bp.total_qty, bp.total_deployed, bp.position_capital_base, bp.frozen_slot_capitals
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
    slot_count = stream["slot_count"]
    if pos.frozen_slot_capitals is not None:
        slot_capitals = pos.frozen_slot_capitals
    else:
        weights = (stream["parameters"].get("slots") or {}).get("slot_capital_weight")
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


def _sum_entry_fees(conn, position_id: int) -> tuple[float, bool]:
    """Real entry-side fees for a position, per-fill (not SUM(fee_usd) -- see
    place_exit's original docstring: a mix of legacy NULL and real fee_usd
    rows would silently undercount via SQL SUM). Returns (total, is_estimated)."""
    fills = conn.execute(
        text("SELECT capital, fee_usd FROM live.blended_fills WHERE position_id = :pid"),
        {"pid": position_id},
    ).fetchall()
    total = 0.0
    estimated = False
    for fill in fills:
        if fill.fee_usd is not None:
            total += float(fill.fee_usd)
        else:
            total += float(fill.capital) * MAKER_FEE  # legacy fill, opened before the fee-capture fix
            estimated = True
    return total, estimated


def _close_position(conn, position, real_exit_price: float, exit_fee_usd: float,
                    fee_is_estimated: bool, exit_reason: str, txid: str,
                    kraken_order_txid: str, stream_name: str, model_id: int, dry_run: bool) -> float:
    """Shared finalize step: record the real fill, update capital ledger, alert.
    Used by both the immediate market-sell (capitulation) path and the
    confirmed-fill callback from check_pending_exit() (armed/trailing-stop path)."""
    total_qty = float(position.total_qty)
    total_deployed = float(position.total_deployed)
    total_entry_fees_usd, entry_fees_estimated = _sum_entry_fees(conn, position.position_id)
    fee_is_estimated = fee_is_estimated or entry_fees_estimated

    pnl = (total_qty * real_exit_price) - exit_fee_usd - total_deployed - total_entry_fees_usd
    closing_capital = total_deployed + pnl

    conn.execute(
        text("""
            UPDATE live.blended_positions
            SET status = 'CLOSED', exit_price = :price, exit_order_id = :txid,
                exit_fee_usd = :exit_fee, fee_is_estimated = :estimated,
                closing_capital = :closing, realized_pnl = :pnl,
                exit_reason = :reason, closed_at = :now,
                pending_exit_order_id = NULL, pending_exit_price = NULL, pending_exit_placed_at = NULL
            WHERE position_id = :pid
        """),
        {"price": real_exit_price, "txid": kraken_order_txid, "exit_fee": round(exit_fee_usd, 4),
         "estimated": fee_is_estimated, "closing": round(closing_capital, 2),
         "pnl": round(pnl, 2), "reason": exit_reason, "now": datetime.now(timezone.utc),
         "pid": position.position_id},
    )

    available_capital = get_available_capital(conn, model_id)
    _update_available_capital(conn, model_id, available_capital + pnl)

    if not dry_run:
        notifier.alert_blend_closed(stream_name, model_id, total_deployed,
                                    round(closing_capital, 2), round(pnl, 2), exit_reason)
    return pnl


def place_exit(conn, position, exit_price: float, exit_reason: str,
              kraken: KrakenClient, dry_run: bool = False,
              stream_name: str = "", model_id: int = 0) -> None:
    """
    Market-sell the whole blended stack and close the position IMMEDIATELY.

    Only used for capitulation (a deliberate, guaranteed forced cut -- real
    urgency to get out, same reasoning as Model 1's hard stop-loss staying a
    market order). The armed/trailing-stop path no longer calls this -- see
    ensure_pending_exit()/check_pending_exit(), which place a real resting
    limit order at the floor instead, since a market sell here could (and
    did, during a live-replay audit) fill far below the intended "never
    voluntarily realize a loss" floor during an active crash.

    exit_price: the computed trigger price -- used as-is in dry run, and as
    the fallback if Kraken's post-placement status poll doesn't confirm a
    real fill yet (market sells fill essentially instantly, so this is rare).
    """
    total_qty = float(position.total_qty)

    if dry_run:
        log.info(f"[DRY RUN] Would market sell position {position.position_id}: "
                 f"{total_qty:.8f} BTC @ ~${exit_price:.2f} ({exit_reason})")
        txid = f"DRY-EXIT-{position.position_id}"
        real_exit_price = exit_price
        exit_fee_usd = total_qty * exit_price * TAKER_FEE
        fee_is_estimated = False
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
            fee_is_estimated = False
        else:
            log.warning(f"Exit order {txid} (position {position.position_id}) not confirmed filled on "
                        "first poll -- using estimated price/fee, flagging for manual reconciliation")
            real_exit_price = exit_price
            exit_fee_usd = total_qty * exit_price * TAKER_FEE
            fee_is_estimated = True

    _close_position(conn, position, real_exit_price, exit_fee_usd, fee_is_estimated,
                    exit_reason, txid, txid, stream_name, model_id, dry_run)


def ensure_pending_exit(conn, position, target_price: float, kraken: KrakenClient,
                        dry_run: bool = False) -> None:
    """
    Place (or re-price) a real resting LIMIT sell for an armed position at
    target_price. Kraken's own order book decides if/when it actually
    fills -- check_pending_exit() polls for that separately. Never finalizes
    the position itself.

    Idempotent: no-op if a pending exit already rests at (essentially) this
    same price; cancels and replaces if the floor has moved (it only ever
    moves up while armed, since HWM never falls and cascade adds only lower
    avg cost, per the ladder/breakeven math in blended_position_monitor.py).
    """
    total_qty = float(position.total_qty)
    current_order_id = position.pending_exit_order_id
    current_price = float(position.pending_exit_price) if position.pending_exit_price is not None else None

    if current_order_id is not None and current_price is not None and abs(current_price - target_price) < 0.01:
        return  # already correctly resting

    if dry_run:
        if current_order_id is not None:
            log.info(f"[DRY RUN] Would re-price pending exit for position {position.position_id}: "
                     f"${current_price:.2f} -> ${target_price:.2f}")
        else:
            log.info(f"[DRY RUN] Would place pending exit limit sell for position {position.position_id} "
                     f"@ ${target_price:.2f}")
        txid = f"DRY-PENDING-EXIT-{position.position_id}"
    else:
        if current_order_id is not None:
            try:
                kraken.cancel_order(current_order_id)
            except Exception as e:
                log.warning(f"Cancel attempt for stale pending exit {current_order_id} "
                            f"(position {position.position_id}) raised: {e}")
        try:
            txid = kraken.place_order("sell", total_qty, price_usd=target_price, order_type="limit")
            log.info(f"Pending exit limit sell {'re-priced' if current_order_id else 'placed'} for "
                     f"position {position.position_id}: {total_qty:.8f} BTC @ ${target_price:.2f} txid={txid}")
        except Exception as e:
            log.error(f"Failed to place pending exit order for position {position.position_id}: {e}")
            return

    conn.execute(
        text("""
            UPDATE live.blended_positions
            SET pending_exit_order_id = :txid, pending_exit_price = :price, pending_exit_placed_at = :now
            WHERE position_id = :pid
        """),
        {"txid": txid, "price": target_price, "now": datetime.now(timezone.utc), "pid": position.position_id},
    )


def check_pending_exit(conn, kraken: KrakenClient, streams: dict, dry_run: bool = False) -> tuple[int, int]:
    """
    Poll Kraken for resting exit limit orders. Finalize the position on a
    real confirmed fill. No expiry/abandon -- unlike entries and adds, an
    armed position's exit can't just be given up on; if unfilled, it keeps
    resting (and gets re-priced by ensure_pending_exit as the floor moves)
    until the market genuinely reaches it.
    """
    now = datetime.now(timezone.utc)
    pending = conn.execute(
        text("""
            SELECT bp.position_id, bp.stream_id, bp.model_id, ls.stream_name,
                   bp.pending_exit_order_id, bp.total_qty, bp.total_deployed
            FROM live.blended_positions bp
            JOIN live.streams ls ON ls.stream_id = bp.stream_id
            WHERE bp.status = 'OPEN' AND bp.pending_exit_order_id IS NOT NULL
        """)
    ).fetchall()

    fills = 0
    still_pending = 0

    for pos in pending:
        if dry_run:
            log.debug(f"[DRY RUN] Skipping exit-fill check for position_id={pos.position_id}")
            continue

        order, _ = _resolve_order(kraken, pos.pending_exit_order_id, None, now,
                                  f"position {pos.position_id} pending exit")
        if order is None:
            still_pending += 1
            continue

        vol_exec = float(order.get("vol_exec", 0) or 0)
        if vol_exec > 0:
            real_exit_price = float(order.get("price", 0) or 0)
            exit_fee_usd = float(order.get("fee", 0) or 0)
            _close_position(conn, pos, real_exit_price, exit_fee_usd, False, "trailing_stop",
                            pos.pending_exit_order_id, pos.pending_exit_order_id,
                            pos.stream_name, pos.model_id, dry_run)
            fills += 1
        else:
            # Still resting -- this is the normal, expected case most ticks.
            still_pending += 1

    return fills, still_pending
