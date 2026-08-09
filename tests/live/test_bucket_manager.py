"""
BTC accumulation bucket (docs/decisions/008) -- bucket_manager.check_dip_buy
and check_principal_recovery, against a real sandboxed live.btc_bucket row
and FakeKraken. Throwaway model_version=996, cleaned up regardless of
pass/fail.

Run:
    pytest tests/live/test_bucket_manager.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import bucket_manager
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 996


@pytest.fixture
def sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- btc bucket', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        conn.execute(text("INSERT INTO live.btc_bucket (model_id) VALUES (:mid)"), {"mid": model_id})

    yield engine, model_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.btc_bucket_events WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.btc_bucket WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def _set_bucket(engine, model_id, cash=0.0, qty=0.0, cost_basis=0.0, house=0.0):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE live.btc_bucket SET bucket_cash=:c, tracked_qty=:q,
                   tracked_cost_basis=:cb, house_money_qty=:h WHERE model_id=:mid
        """), {"c": cash, "q": qty, "cb": cost_basis, "h": house, "mid": model_id})


def test_no_bucket_row_is_a_safe_no_op():
    """A model with no live.btc_bucket row at all must not error."""
    engine = _get_engine()
    kraken = FakeKraken()
    with engine.begin() as conn:
        assert bucket_manager.check_dip_buy(conn, model_id=999999, price=50000.0,
                                            drawdown_from_high_pct=-20.0, kraken=kraken, dry_run=False) is False
        assert bucket_manager.check_principal_recovery(conn, model_id=999999, price=50000.0,
                                                        kraken=kraken, dry_run=False) is False


def test_dip_buy_does_not_fire_above_threshold(sandbox):
    engine, model_id = sandbox
    _set_bucket(engine, model_id, cash=20.0)
    kraken = FakeKraken()
    with engine.begin() as conn:
        fired = bucket_manager.check_dip_buy(conn, model_id, price=50000.0,
                                             drawdown_from_high_pct=-10.0,  # shallower than 15% threshold
                                             kraken=kraken, dry_run=False)
    assert fired is False
    with engine.begin() as conn:
        row = conn.execute(text("SELECT bucket_cash FROM live.btc_bucket WHERE model_id=:mid"),
                           {"mid": model_id}).fetchone()
    assert abs(float(row.bucket_cash) - 20.0) < 1e-6


def test_dip_buy_does_not_fire_below_min_capital(sandbox):
    engine, model_id = sandbox
    _set_bucket(engine, model_id, cash=5.0)  # below MIN_BUY_CAPITAL=10
    kraken = FakeKraken()
    with engine.begin() as conn:
        fired = bucket_manager.check_dip_buy(conn, model_id, price=50000.0,
                                             drawdown_from_high_pct=-20.0, kraken=kraken, dry_run=False)
    assert fired is False


def test_dip_buy_fires_and_updates_tracked_position(sandbox):
    engine, model_id = sandbox
    _set_bucket(engine, model_id, cash=20.0)
    kraken = FakeKraken()
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        fired = bucket_manager.check_dip_buy(conn, model_id, price=50000.0,
                                             drawdown_from_high_pct=-16.0,  # past the 15% threshold
                                             kraken=kraken, dry_run=False)
    assert fired is True
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT bucket_cash, tracked_qty, tracked_cost_basis FROM live.btc_bucket WHERE model_id=:mid
        """), {"mid": model_id}).fetchone()
        event = conn.execute(text("SELECT event_type, amount_usd, qty_btc FROM live.btc_bucket_events WHERE model_id=:mid"),
                             {"mid": model_id}).fetchone()
    assert abs(float(row.bucket_cash)) < 1e-6              # fully spent
    assert abs(float(row.tracked_qty) - 20.0 / 50000.0) < 1e-8
    assert abs(float(row.tracked_cost_basis) - 20.0) < 1e-6
    assert event.event_type == "buy"
    assert abs(float(event.amount_usd) - 20.0) < 1e-6


def test_principal_recovery_does_not_fire_below_premium(sandbox):
    engine, model_id = sandbox
    _set_bucket(engine, model_id, qty=0.001, cost_basis=50.0)  # bought at $50k
    kraken = FakeKraken()
    with engine.begin() as conn:
        # only +30% -- below the 50% premium threshold
        fired = bucket_manager.check_principal_recovery(conn, model_id, price=50000.0 * 1.30,
                                                         kraken=kraken, dry_run=False)
    assert fired is False


def test_principal_recovery_fires_and_banks_house_money(sandbox):
    engine, model_id = sandbox
    entry_price = 50000.0
    qty = 20.0 / entry_price
    _set_bucket(engine, model_id, qty=qty, cost_basis=20.0)
    kraken = FakeKraken()
    exit_price = entry_price * 1.60  # +60%, past the 50% premium threshold
    kraken._next_price = exit_price
    with engine.begin() as conn:
        fired = bucket_manager.check_principal_recovery(conn, model_id, price=exit_price,
                                                         kraken=kraken, dry_run=False)
    assert fired is True
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT bucket_cash, tracked_qty, tracked_cost_basis, house_money_qty FROM live.btc_bucket WHERE model_id=:mid
        """), {"mid": model_id}).fetchone()
        event = conn.execute(text("SELECT event_type FROM live.btc_bucket_events WHERE model_id=:mid"),
                             {"mid": model_id}).fetchone()
    assert float(row.tracked_qty) == 0.0
    assert float(row.tracked_cost_basis) == 0.0
    assert float(row.house_money_qty) > 0.0          # remainder banked as permanent house money
    assert float(row.bucket_cash) > 0.0               # principal recovered back into bucket cash
    assert event.event_type == "recover_principal"
