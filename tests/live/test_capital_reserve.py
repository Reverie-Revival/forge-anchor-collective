"""
Pooled capital reserve (docs/decisions/008) -- order_manager.place_entry's
reserve-aware sizing, place_exit's pool updates, and the hard-floor halt.

Opt-in: a model with no live.capital_reserve row must trade exactly as
before (plain fixed lot_size_usd, no reserve logic at all). Every test here
uses a throwaway model_version=997 row, cleaned up regardless of pass/fail.

Run:
    pytest tests/live/test_capital_reserve.py -v -s
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import order_manager
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 997

PARAMS = {
    "filters": {}, "position": {"trailing_stop_pct": 5.0, "entry_expiry_candles": 2},
    "sentiment": False, "core_params": {}, "core_signal": "test_signal", "primary_timeframe": "1h",
}


@pytest.fixture
def sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- capital reserve', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'TEST Reserve Stream', 'v1', 'ema_crossover',
                    CAST(:params AS jsonb), 1, 'single', 25.0)
            RETURNING stream_id
        """), {"mid": model_id, "params": json.dumps(PARAMS)}).scalar()

    stream = {"stream_id": stream_id, "model_id": model_id, "stream_name": "TEST Reserve Stream",
              "parameters": PARAMS, "lot_size_usd": 25.0}

    yield engine, stream, model_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.lots WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.btc_bucket_events WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.btc_bucket WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.capital_reserve WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def _provision_reserve(engine, model_id, baseline_total, pool_balance, hard_floor=40.0, halted_at=None):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO live.capital_reserve (model_id, baseline_total, pool_balance, hard_floor, halted_at)
            VALUES (:mid, :baseline, :pool, :floor, :halted)
        """), {"mid": model_id, "baseline": baseline_total, "pool": pool_balance,
               "floor": hard_floor, "halted": halted_at})


def test_no_reserve_row_falls_back_to_full_lot_size(sandbox):
    """No live.capital_reserve row at all -- must trade exactly as before."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)

    with engine.begin() as conn:
        lot = conn.execute(text("SELECT opening_capital FROM live.lots WHERE stream_id = :sid"),
                           {"sid": stream["stream_id"]}).fetchone()
    assert float(lot.opening_capital) == 25.0


def test_pool_at_baseline_uses_full_lot_size(sandbox):
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=100.0)
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)

    with engine.begin() as conn:
        lot = conn.execute(text("SELECT opening_capital FROM live.lots WHERE stream_id = :sid"),
                           {"sid": stream["stream_id"]}).fetchone()
    assert float(lot.opening_capital) == 25.0


def test_pool_below_baseline_shrinks_proportionally(sandbox):
    """weight = 25/100 = 0.25; pool=$60 -> entry should be $15."""
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=60.0)
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)

    with engine.begin() as conn:
        lot = conn.execute(text("SELECT opening_capital, btc_quantity FROM live.lots WHERE stream_id = :sid"),
                           {"sid": stream["stream_id"]}).fetchone()
    assert abs(float(lot.opening_capital) - 15.0) < 1e-6
    assert abs(float(lot.btc_quantity) - 15.0 / 50000.0) < 1e-9


def test_pool_share_below_ten_skips_entry_entirely(sandbox):
    """weight = 0.25; pool=$39 -> share = $9.75 < $10 -- must skip, no lot row at all."""
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=39.0)
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM live.lots WHERE stream_id = :sid"),
                             {"sid": stream["stream_id"]}).scalar()
    assert count == 0


def test_exit_updates_pool_balance_by_realized_pnl(sandbox):
    engine, stream, model_id = sandbox
    # pool below baseline (entry still sized fine -- weight 0.25 * 90 = 22.5
    # >= $10) and the resulting small gain stays well under baseline, so no
    # surplus/skim triggers here -- that has its own dedicated test below.
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=90.0)
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending(conn, kraken, dry_run=False)

    armed_close = 50000.0 * 1.05
    stop_trigger_low = armed_close * (1 - 0.05) - 1
    kraken._next_price = armed_close
    from src.live import position_monitor
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"1h"}, kraken, dry_run=False)

    with engine.begin() as conn:
        lot = conn.execute(text("SELECT realized_pnl FROM live.lots WHERE stream_id = :sid"),
                           {"sid": stream["stream_id"]}).fetchone()
        reserve = conn.execute(text("SELECT pool_balance FROM live.capital_reserve WHERE model_id = :mid"),
                               {"mid": model_id}).fetchone()
    assert abs(float(reserve.pool_balance) - (90.0 + float(lot.realized_pnl))) < 1e-6


def test_surplus_pushes_skim_into_bucket_when_provisioned(sandbox):
    """pool starts exactly at baseline -- the ENTIRE gain from a win is
    surplus, so the entire (rate-limited) skim should land in the bucket,
    matching the same dynamic_skim formula as the backtest side."""
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=100.0)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO live.btc_bucket (model_id) VALUES (:mid)"), {"mid": model_id})

    with engine.begin() as conn:
        order_manager._update_reserve(conn, model_id, pnl=50.0)

    raw_pct = 10.0 / ((1.8 / 100.0) * 150.0 * 22) * 100.0
    expected_skim_pct = max(10.0, min(25.0, raw_pct))
    expected_skim = 50.0 * expected_skim_pct / 100.0

    with engine.begin() as conn:
        reserve = conn.execute(text("SELECT pool_balance FROM live.capital_reserve WHERE model_id = :mid"),
                               {"mid": model_id}).fetchone()
        bucket = conn.execute(text("SELECT bucket_cash FROM live.btc_bucket WHERE model_id = :mid"),
                              {"mid": model_id}).fetchone()
        event = conn.execute(text("SELECT event_type, amount_usd FROM live.btc_bucket_events WHERE model_id = :mid"),
                             {"mid": model_id}).fetchone()

    # 1 cent tolerance -- these columns are NUMERIC(12,2), rounded in the DB
    assert abs(float(bucket.bucket_cash) - expected_skim) < 0.01
    assert abs(float(reserve.pool_balance) - (150.0 - expected_skim)) < 0.01
    assert event.event_type == "skim"
    assert abs(float(event.amount_usd) - expected_skim) < 0.01


def test_surplus_stays_in_pool_when_no_bucket_provisioned(sandbox):
    """Reserve exists, bucket doesn't -- surplus must just stay as pool cash,
    not silently vanish or error."""
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=100.0)

    with engine.begin() as conn:
        order_manager._update_reserve(conn, model_id, pnl=50.0)

    with engine.begin() as conn:
        reserve = conn.execute(text("SELECT pool_balance FROM live.capital_reserve WHERE model_id = :mid"),
                               {"mid": model_id}).fetchone()
    assert abs(float(reserve.pool_balance) - 150.0) < 1e-6


def test_pool_crossing_hard_floor_sets_halted_at_once_and_stays_set(sandbox):
    """A loss that pushes the pool below hard_floor sets halted_at. A later
    win must NOT clear it -- first-time-only, permanent, matches
    docs/decisions/008 ("this does not self-recover")."""
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=41.0, hard_floor=40.0)

    with engine.begin() as conn:
        order_manager._update_reserve(conn, model_id, pnl=-2.0)  # 41 - 2 = 39 < 40
    with engine.begin() as conn:
        row = conn.execute(text("SELECT pool_balance, halted_at FROM live.capital_reserve WHERE model_id = :mid"),
                           {"mid": model_id}).fetchone()
    assert row.halted_at is not None
    assert abs(float(row.pool_balance) - 39.0) < 1e-6
    first_halted_at = row.halted_at

    with engine.begin() as conn:
        order_manager._update_reserve(conn, model_id, pnl=+5.0)  # a later win moves the balance...
    with engine.begin() as conn:
        row = conn.execute(text("SELECT pool_balance, halted_at FROM live.capital_reserve WHERE model_id = :mid"),
                           {"mid": model_id}).fetchone()
    assert abs(float(row.pool_balance) - 44.0) < 1e-6
    assert row.halted_at == first_halted_at  # ...but halted_at must NOT reset


def test_halted_model_still_skips_entries(sandbox):
    """Once halted, entries stay skipped even if a stray win nudges the pool
    back near baseline -- halted_at is informational/alerting, not itself
    a re-gate on entry sizing (entry sizing is purely pool_balance-vs-weight,
    same as always; this just confirms the two mechanisms don't fight)."""
    engine, stream, model_id = sandbox
    _provision_reserve(engine, model_id, baseline_total=100.0, pool_balance=5.0, hard_floor=40.0,
                       halted_at="2026-01-01T00:00:00+00:00")
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM live.lots WHERE stream_id = :sid"),
                             {"sid": stream["stream_id"]}).scalar()
    assert count == 0
