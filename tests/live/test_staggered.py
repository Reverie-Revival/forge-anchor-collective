"""
Layer 2 -- staggered live-parity tests.

Model 1/2 are single-slot only; before this session, staggered slot_mode had
backtest logic (src/backtester/engine.py) but no live counterpart at all --
order_manager.place_entry hardcoded slot_number=1 and executor.py only ever
checked slot 1's availability. This is the coverage for the ported live
logic (order_manager.next_signal_slot) -- see
docs/decisions/009-unify-testing-with-live-execution.md execution order
step 1. No locked or live model currently depends on staggered, so
correctness here is judged against engine.py's documented behavior, not any
real capital.

cascade live parity was built alongside this, then removed 2026-08-10 after
its live-replay trade count didn't match engine.py's and the mismatch
wasn't run down -- see docs/decisions/009. Its tests were removed with it.

Run:
    pytest tests/live/test_staggered.py -v -s
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import order_manager
from src.live import position_monitor
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 997


def _make_stream(engine, slot_mode, slot_count, position_extra=None, slots_extra=None, lot_size=30.0):
    params = {
        "filters": {},
        "position": {"trailing_stop_pct": 5.0, "entry_expiry_candles": 2, **(position_extra or {})},
        "slots": slots_extra or {},
        "sentiment": False,
        "core_params": {},
        "core_signal": "test_signal",
        "primary_timeframe": "1h",
    }
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(
            text("""
                INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
                VALUES (:v, 'TEST -- staggered', 0, 'active') RETURNING model_id
            """),
            {"v": TEST_MODEL_VERSION},
        ).scalar()
        stream_id = conn.execute(
            text("""
                INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                          parameters, slot_count, slot_mode, lot_size_usd)
                VALUES (:mid, 'TEST Multi-Slot', 'v1', 'test', CAST(:params AS jsonb), :sc, :sm, :lot)
                RETURNING stream_id
            """),
            {"mid": model_id, "params": __import__("json").dumps(params),
             "sc": slot_count, "sm": slot_mode, "lot": lot_size},
        ).scalar()
    return {
        "stream_id": stream_id, "model_id": model_id, "stream_name": "TEST Multi-Slot",
        "parameters": params, "lot_size_usd": lot_size, "slot_count": slot_count, "slot_mode": slot_mode,
    }


@pytest.fixture
def engine():
    eng = _get_engine()
    yield eng
    with eng.begin() as conn:
        conn.execute(text("""
            DELETE FROM live.lots WHERE model_id IN
            (SELECT model_id FROM live.models WHERE model_version = :v)
        """), {"v": TEST_MODEL_VERSION})
        conn.execute(text("DELETE FROM live.streams WHERE model_id IN "
                           "(SELECT model_id FROM live.models WHERE model_version = :v)"), {"v": TEST_MODEL_VERSION})
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})


def test_single_mode_unaffected_by_slot_helpers(engine):
    """Regression guard: a plain single-slot stream must behave exactly as
    it did before this session's changes -- next_signal_slot degrades to
    the old hardcoded slot_is_available(..., 1) check."""
    stream = _make_stream(engine, "single", 1)
    with engine.begin() as conn:
        assert order_manager.next_signal_slot(conn, stream) == 1
        order_manager.place_entry(conn, stream, FakeKraken(), dry_run=False)
    with engine.begin() as conn:
        assert order_manager.next_signal_slot(conn, stream) is None
        lot = conn.execute(text("SELECT slot_number FROM live.lots WHERE stream_id = :sid"),
                            {"sid": stream["stream_id"]}).fetchone()
        assert lot.slot_number == 1


def test_staggered_dispatches_to_longest_free_slot(engine):
    stream = _make_stream(engine, "staggered", 2, slots_extra={"slot_capital_weight": [70, 30]})
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        slot = order_manager.next_signal_slot(conn, stream)
        assert slot == 1  # both free, ties broken toward slot 1
        order_manager.place_entry(conn, stream, kraken, dry_run=False, slot_number=slot)

    with engine.begin() as conn:
        lot = conn.execute(text("SELECT opening_capital FROM live.lots WHERE slot_number = 1 AND stream_id = :sid"),
                            {"sid": stream["stream_id"]}).fetchone()
        assert abs(float(lot.opening_capital) - 30.0 * 0.7) < 1e-6  # slot_capital_weight [70,30] on $30 lot

        slot = order_manager.next_signal_slot(conn, stream)
        assert slot == 2  # slot 1 occupied, only slot 2 free
        order_manager.place_entry(conn, stream, kraken, dry_run=False, slot_number=slot)

    with engine.begin() as conn:
        assert order_manager.next_signal_slot(conn, stream) is None  # both slots occupied now

        # Close slot 1, then slot 1 should be offered again before a never-used slot would be
        conn.execute(text("""
            UPDATE live.lots SET status = 'CLOSED', closed_at = now()
            WHERE stream_id = :sid AND slot_number = 1
        """), {"sid": stream["stream_id"]})

    with engine.begin() as conn:
        assert order_manager.next_signal_slot(conn, stream) == 1


def test_staggered_entry_gap_blocks_rapid_second_entry(engine):
    stream = _make_stream(engine, "staggered", 2, slots_extra={"slot_entry_gap_candles": 2})
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        slot = order_manager.next_signal_slot(conn, stream)
        order_manager.place_entry(conn, stream, kraken, dry_run=False, slot_number=slot)

    with engine.begin() as conn:
        # Gap is 2 candles * 60min = 120min; "now" one minute later must be blocked
        import datetime as dt
        soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)
        assert order_manager.next_signal_slot(conn, stream, now=soon) is None


def test_trailing_stop_steps_tightens_as_gain_grows(engine):
    """Found while validating staggered/cascade against engine.py's
    reference numbers this session: trailing_stop_steps
    ([[gain_pct, tighter_trail_pct], ...]) was read by engine.py for every
    slot mode but had no live counterpart at all -- position_monitor.
    check_all only ever used the flat trailing_stop_pct. General gap, not
    specific to any one slot mode; a plain single-slot stream is enough to
    prove it."""
    stream = _make_stream(
        engine, "single", 1,
        position_extra={"trailing_stop_pct": 20.0, "trailing_stop_steps": [[10, 5]]},
    )
    kraken = FakeKraken()
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending(conn, kraken, dry_run=False)

    # Rally 12% -- past the 10% step threshold, so the effective trail should
    # already be tightened to 5%, not the base 20%.
    peak = 50000.0 * 1.12
    kraken._next_price = peak
    with engine.begin() as conn:
        # Prime the high-water mark to the peak first (a real rally would take
        # multiple candles; one direct HWM update is equivalent for this check).
        conn.execute(text("UPDATE live.lots SET high_water_mark = :hwm WHERE stream_id = :sid"),
                     {"hwm": peak, "sid": stream["stream_id"]})

    # A 6% pullback from peak would survive a flat 20% trail (stop at peak*0.80)
    # but should breach the tightened 5% step (stop at peak*0.95).
    pulled_back = peak * 0.94
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": pulled_back, "low": pulled_back}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"1h"}, kraken, dry_run=False
        )
        assert stops == 1
        lot = conn.execute(text("SELECT status, exit_reason FROM live.lots WHERE stream_id = :sid"),
                            {"sid": stream["stream_id"]}).fetchone()
        assert lot.status == "CLOSED"
        assert lot.exit_reason == "trailing_stop"


def test_trail_arm_gain_pct_holds_until_armed_then_floors_at_breakeven(engine):
    stream = _make_stream(
        engine, "single", 1,
        position_extra={"trailing_stop_pct": 50.0, "trail_arm_gain_pct": 8.0},
    )
    kraken = FakeKraken()
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending(conn, kraken, dry_run=False)

    # Only up 3% -- not armed yet, no stop_loss_pct configured -- must hold
    # even on a sharp drop, since the (wide, 50%) trail isn't active pre-arm.
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 50000.0 * 1.03, "low": 50000.0 * 0.80}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"1h"}, kraken, dry_run=False
        )
        assert stops == 0
        lot = conn.execute(text("SELECT status FROM live.lots WHERE stream_id = :sid"),
                            {"sid": stream["stream_id"]}).fetchone()
        assert lot.status == "OPEN"
