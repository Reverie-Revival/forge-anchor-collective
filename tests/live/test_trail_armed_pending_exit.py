"""
Layer 2c — the trail_armed / real-limit-exit fix, live-code integration tests.

Built after a live-replay audit (tools/live_replay/replay_gauntlet.py) found
that place_exit()'s unconditional MARKET sell could (and did) fill far below
the intended "never voluntarily realize a loss" floor during an active
crash -- the floor is computed from a stale high-water-mark, and a market
sell takes whatever price is available right then, not the computed floor.
Fixed by making the armed/trailing-stop exit a REAL resting limit order
(ensure_pending_exit/check_pending_exit) instead, and by persisting
trail_armed so capitulation becomes permanently unreachable once a position
has proven it can arm (a real profit floor exists).

None of this is covered by test_blended_state_machine.py's existing
scenarios (which only ever test the immediate-close case) or
test_model4_mechanics.py (Model 4's four unrelated mechanisms) -- this file
covers the three behaviors specific to this fix:
  1. Cascade adds keep working after arming (a new, cheaper fill only lowers
     the floor, never raises it -- no conflict with a resting exit).
  2. Capitulation is permanently unreachable once armed, even if all slots
     later fill during a continuing crash.
  3. A resting pending-exit order re-prices (cancel + replace) when the
     floor moves further (HWM continues climbing while unfilled).

Run:
    pytest tests/live/test_trail_armed_pending_exit.py -v -s
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import blended_order_manager as order_manager
from src.live import blended_position_monitor as position_monitor
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 994

PARAMS = {
    "slots": {"slot_capital_weight": [20, 20, 20, 20, 20]},
    "filters": {},
    "position": {
        "compound": True,
        "trailing_stop_pct": 5.0,
        "trail_arm_gain_pct": 4,
        "cumulative_drop_pcts": [1, 2, 5, 10],
        "entry_expiry_candles": 2,
        "capitulation_stop_pct": 15,
    },
    "sentiment": False,
    "core_params": {"dip_pct": 1.0},
    "core_signal": "fear_dip",
    "primary_timeframe": "4h",
}


@pytest.fixture
def sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- trail_armed / pending exit', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'TEST Trail Armed', 'v8', 'blended_dca', CAST(:p AS jsonb), 5, 'blended', 20.0)
            RETURNING stream_id
        """), {"mid": model_id, "p": json.dumps(PARAMS)}).scalar()
        conn.execute(text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, 100.00)"),
                     {"mid": model_id})

    stream = {
        "stream_id": stream_id, "model_id": model_id, "stream_name": "TEST Trail Armed",
        "parameters": PARAMS, "slot_count": 5, "slot_mode": "blended", "lot_size_usd": 20.0,
    }

    yield engine, stream, model_id

    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM live.blended_fills WHERE position_id IN
            (SELECT position_id FROM live.blended_positions WHERE model_id = :mid)
        """), {"mid": model_id})
        conn.execute(text("DELETE FROM live.blended_positions WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.blended_capital WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def test_cascade_add_continues_after_arming(sandbox):
    """Arm on slot 1 alone (a quick rally), then let price crash past the
    slot-2 cascade trigger. The add must still fire -- arming does NOT freeze
    composition -- and the new, lower average cost must be reflected."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # Rally 6% -- arms (4% threshold), places a resting pending exit.
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 53000.0, "low": 52900.0}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT trail_armed, pending_exit_order_id FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.trail_armed is True
        assert pos.pending_exit_order_id is not None
        first_pending_order_id = pos.pending_exit_order_id

    # Now crash past the slot-2 trigger (1% below the 50000 original entry).
    kraken._next_price = 49000.0
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49000.0, kraken=kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT pending_add_order_id, pending_add_index FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.pending_add_order_id is not None, "cascade add must still fire even though the position is armed"
        assert pos.pending_add_index == 1

    with engine.begin() as conn:
        fills, _ = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1
        pos = conn.execute(text("""
            SELECT avg_cost_basis, trail_armed, pending_exit_order_id FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert float(pos.avg_cost_basis) < 50000.0   # the add genuinely lowered the average
        assert pos.trail_armed is True                # still armed -- never un-arms
        # The pending exit from before the add is untouched by the add itself --
        # check_all (not check_cascade_add_trigger) is what re-prices it, next tick.
        assert pos.pending_exit_order_id == first_pending_order_id

    print("\nCascade-add-after-arming OK -- add fired, average lowered, still armed.")


def test_capitulation_never_fires_once_armed(sandbox):
    """Arm on slot 1 alone, then crash hard enough to fill every remaining
    slot AND blow through what would be the capitulation line. Capitulation
    must never fire -- once armed, only the resting limit exit can close
    this position, no matter how far price falls afterward."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # Rally 6% -- arms. next_fill_mode="none" so the resting exit limit stays
    # unfilled (FakeKraken's default "full" mode fills instantly, which would
    # let the subsequent ensure_pending_exit fill-check discover it "filled"
    # immediately and finalize the position -- not what this test is about).
    kraken.next_fill_mode = "none"
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 53000.0, "low": 52900.0}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)

    # Crash through all 4 remaining cascade triggers -- fills every slot.
    for price in (49000.0, 48000.0, 47000.0, 44000.0):
        kraken._next_price = price
        with engine.begin() as conn:
            order_manager.check_cascade_add_trigger(conn, stream, latest_close=price, kraken=kraken, dry_run=False)
        with engine.begin() as conn:
            fills, _ = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
            assert fills == 1

    with engine.begin() as conn:
        pos = conn.execute(text("""
            SELECT capitulation_armed, trail_armed FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.capitulation_armed is True   # all 5 slots filled -- would normally enable capitulation
        assert pos.trail_armed is True           # armed from the very first rally, still armed

    # Crash WAY past what the (disabled) capitulation line would be --
    # last fill 44000, capitulation_stop_pct=15 -> would-be line = 37400.
    # Go to 20000, far below that, and confirm it still does NOT fire.
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 20000.0, "low": 19500.0}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 0   # NOT a capitulation exit
        pos = conn.execute(text("""
            SELECT status, exit_reason FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "OPEN"          # still open -- never force-closed at a loss
        assert pos.exit_reason is None

    print("\nArmed-blocks-capitulation OK -- crashed through the would-be capitulation line, position still open.")


def test_pending_exit_reprices_when_floor_moves(sandbox):
    """Once armed, if price keeps climbing further (HWM rises), the resting
    exit order must be cancelled and replaced at the new, higher floor --
    not left stale at the original (now too-cheap) price."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # Rally 6% -- arms, places the first resting exit. next_fill_mode="none" so
    # it stays resting/unfilled (FakeKraken's default "full" mode fills
    # instantly, which would let ensure_pending_exit's fill-check discover it
    # "filled" on the very next call instead of re-pricing it).
    kraken.next_fill_mode = "none"
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 53000.0, "low": 52900.0}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT pending_exit_order_id, pending_exit_price FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        first_order_id = pos.pending_exit_order_id
        first_price = float(pos.pending_exit_price)
        assert first_order_id is not None

    # Rally further -- new HWM pushes the 5%-trail floor above the old one.
    # Still "none" so the re-priced order also stays resting for inspection.
    kraken.next_fill_mode = "none"
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 58000.0, "low": 57900.0}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT pending_exit_order_id, pending_exit_price FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert float(pos.pending_exit_price) > first_price, "floor should have moved up with the new HWM"
        assert pos.pending_exit_order_id != first_order_id, "stale order must be cancelled and replaced, not left resting"

    print(f"\nRe-price OK -- exit floor moved ${first_price:.2f} -> ${float(pos.pending_exit_price):.2f}, "
          f"order replaced ({first_order_id} -> {pos.pending_exit_order_id}).")
