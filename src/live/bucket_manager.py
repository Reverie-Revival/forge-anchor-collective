"""
Live BTC accumulation bucket (docs/decisions/008) -- funded from
live.capital_reserve's surplus above baseline (see
order_manager._update_reserve, which calls add_skim()). Buys real BTC on a
real dip (drawdown_from_high_pct off a real rolling daily high, stricter
than any stream's own entry signal), sells only enough to recover its own
principal once a real premium clears, remainder becomes permanent house
money -- principal can never be lost twice, only realized profit stays
permanently exposed to BTC.

Mirrors src/backtester/model_engine.py's simulate_skim_bucket loop exactly
(see docs/decisions/007 section 4 for the original mechanics/tuning: 15%
dip trigger off a 60-day high, 50% sell premium).

Opt-in, model-agnostic: every function here takes model_id explicitly and
no-ops if that model has no live.btc_bucket row. Any executor script (Model
1's today, a future Model 2's) can call these the same way -- the bucket
isn't tied to any one model's executor.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from src.live.kraken_client import KrakenClient
from src.live import notifier

log = logging.getLogger(__name__)

# Tuned values from docs/decisions/007 section 4 -- see HANDOFF.md 2026-08-06
# for the sweep (8/10/12/15/20% dip thresholds, 10/20/25/50/100% premiums).
DIP_THRESHOLD_PCT = 15.0
SELL_PREMIUM_PCT = 50.0
MIN_BUY_CAPITAL = 10.0


def get_bucket_state(conn, model_id: int):
    """Return this model's bucket row, or None if it hasn't been provisioned
    -- callers must no-op when this returns None."""
    return conn.execute(text("""
        SELECT bucket_cash, tracked_qty, tracked_cost_basis, house_money_qty
        FROM live.btc_bucket WHERE model_id = :mid
    """), {"mid": model_id}).fetchone()


def add_skim(conn, model_id: int, amount: float) -> None:
    """Move `amount` into this model's bucket cash. Caller
    (order_manager._update_reserve) is responsible for having already
    subtracted it from the pooled reserve's pool_balance -- this only
    records where it went. No-ops if this model has no bucket row (i.e. the
    reserve is provisioned but the bucket isn't -- surplus just stays in
    the pool in that case)."""
    if get_bucket_state(conn, model_id) is None:
        return
    conn.execute(text("""
        UPDATE live.btc_bucket SET bucket_cash = bucket_cash + :amt, updated_at = :now
        WHERE model_id = :mid
    """), {"amt": amount, "mid": model_id, "now": datetime.now(timezone.utc)})
    conn.execute(text("""
        INSERT INTO live.btc_bucket_events (model_id, event_type, amount_usd)
        VALUES (:mid, 'skim', :amt)
    """), {"mid": model_id, "amt": amount})


def check_dip_buy(conn, model_id: int, price: float, drawdown_from_high_pct,
                  kraken: KrakenClient, dry_run: bool = False) -> bool:
    """Called once per tick with the current BTC price and its real % drawdown
    from a rolling high (caller computes this off real daily candles -- see
    executor.py's _bucket_tick). Returns True if a buy fired."""
    state = get_bucket_state(conn, model_id)
    if state is None:
        return False
    bucket_cash = float(state.bucket_cash)
    if bucket_cash < MIN_BUY_CAPITAL:
        return False
    if drawdown_from_high_pct is None or drawdown_from_high_pct > -DIP_THRESHOLD_PCT:
        return False

    qty_est = bucket_cash / price

    if dry_run:
        txid = f"DRY-BUCKET-BUY-{model_id}"
        fill_price, fill_qty = price, qty_est
        log.info(f"[DRY RUN] Would buy BTC bucket dip: Model {model_id} "
                 f"${bucket_cash:.2f} @ ${price:.2f} ({fill_qty:.8f} BTC)")
    else:
        try:
            txid = kraken.place_order("buy", qty_est, order_type="market")
            order = kraken.get_order_status(txid)
            fill_price = float(order.get("price", price) or price)
            fill_qty = float(order.get("vol_exec", qty_est) or qty_est)
        except Exception as e:
            log.error(f"Bucket buy failed for model {model_id}: {e}")
            return False

    conn.execute(text("""
        UPDATE live.btc_bucket
        SET bucket_cash = 0, tracked_qty = tracked_qty + :qty,
            tracked_cost_basis = tracked_cost_basis + :cash, updated_at = :now
        WHERE model_id = :mid
    """), {"qty": fill_qty, "cash": bucket_cash, "mid": model_id, "now": datetime.now(timezone.utc)})
    conn.execute(text("""
        INSERT INTO live.btc_bucket_events (model_id, event_type, amount_usd, qty_btc, price, order_id)
        VALUES (:mid, 'buy', :cash, :qty, :price, :txid)
    """), {"mid": model_id, "cash": bucket_cash, "qty": fill_qty, "price": fill_price, "txid": txid})

    if not dry_run:
        notifier.alert_bucket_buy(model_id, bucket_cash, fill_price, fill_qty)
    log.info(f"Bucket buy: Model {model_id} ${bucket_cash:.2f} @ ${fill_price:.2f} ({fill_qty:.8f} BTC)")
    return True


def check_principal_recovery(conn, model_id: int, price: float,
                             kraken: KrakenClient, dry_run: bool = False) -> bool:
    """If the bucket's tracked BTC is worth SELL_PREMIUM_PCT more than its
    own cost basis, sell exactly enough to recover the cost basis as cash
    (back into the bucket, waiting for the next dip) -- the remainder
    becomes permanent house money, never sold again. Returns True if a
    sell fired."""
    state = get_bucket_state(conn, model_id)
    if state is None:
        return False
    tracked_qty = float(state.tracked_qty)
    tracked_cost_basis = float(state.tracked_cost_basis)
    if tracked_cost_basis <= 0:
        return False

    current_value = tracked_qty * price
    if current_value < tracked_cost_basis * (1 + SELL_PREMIUM_PCT / 100.0):
        return False

    qty_to_sell = min(tracked_cost_basis / price, tracked_qty)

    if dry_run:
        from src.fees import TAKER_FEE
        txid = f"DRY-BUCKET-SELL-{model_id}"
        fill_price, fill_qty = price, qty_to_sell
        cash_recovered = fill_qty * fill_price * (1 - TAKER_FEE)
    else:
        try:
            txid = kraken.place_order("sell", qty_to_sell, order_type="market")
            order = kraken.get_order_status(txid)
            fill_price = float(order.get("price", price) or price)
            fill_qty = float(order.get("vol_exec", qty_to_sell) or qty_to_sell)
            fee = float(order.get("fee", 0) or 0)
            cash_recovered = fill_qty * fill_price - fee
        except Exception as e:
            log.error(f"Bucket principal recovery failed for model {model_id}: {e}")
            return False

    house_money_added = tracked_qty - fill_qty

    conn.execute(text("""
        UPDATE live.btc_bucket
        SET bucket_cash = bucket_cash + :cash, tracked_qty = 0, tracked_cost_basis = 0,
            house_money_qty = house_money_qty + :house, updated_at = :now
        WHERE model_id = :mid
    """), {"cash": cash_recovered, "house": house_money_added, "mid": model_id,
           "now": datetime.now(timezone.utc)})
    conn.execute(text("""
        INSERT INTO live.btc_bucket_events (model_id, event_type, amount_usd, qty_btc, price, order_id)
        VALUES (:mid, 'recover_principal', :cash, :qty, :price, :txid)
    """), {"mid": model_id, "cash": cash_recovered, "qty": fill_qty, "price": fill_price, "txid": txid})

    if not dry_run:
        notifier.alert_bucket_recovery(model_id, cash_recovered, house_money_added, fill_price)
    log.info(f"Bucket principal recovered: Model {model_id} ${cash_recovered:.2f} @ ${fill_price:.2f}, "
             f"+{house_money_added:.8f} BTC house money")
    return True
