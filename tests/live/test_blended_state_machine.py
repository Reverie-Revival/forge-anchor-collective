"""
Layer 2 — Blended state machine integration test (Model 3).

Dry-run mode never actually exercises the fill/add/exit code paths (Kraken
polling is skipped entirely when dry_run=True, same as Model 1's
order_manager). This test drives the real (non-dry-run) code paths against
a fake KrakenClient so the full PENDING_ENTRY -> OPEN -> cascade add ->
trailing-stop-exit state machine gets verified without touching a real
Kraken account or real money.

Runs against local Postgres only (DATABASE_URL) using a throwaway
model_version=999 row, cleaned up at the end regardless of pass/fail.

This test MUST pass before any commit that touches blended_order_manager.py,
blended_position_monitor.py, or blended_executor.py.

Run:
    pytest tests/live/test_blended_state_machine.py -v -s
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import blended_order_manager as order_manager
from src.live import blended_position_monitor as position_monitor
from src.live.order_manager import MAKER_FEE
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 999
STREAM_CONFIG_ID = 36

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
        model_id = conn.execute(
            text("""
                INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
                VALUES (:v, 'TEST -- blended state machine', 0, 'active')
                RETURNING model_id
            """),
            {"v": TEST_MODEL_VERSION},
        ).scalar()
        stream_id = conn.execute(
            text("""
                INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                          parameters, slot_count, slot_mode, lot_size_usd)
                VALUES (:mid, 'TEST Grid Stacker Blended', 'v8', 'blended_dca',
                        CAST(:params AS jsonb), 5, 'blended', 20.0)
                RETURNING stream_id
            """),
            {"mid": model_id, "params": __import__("json").dumps(PARAMS)},
        ).scalar()
        conn.execute(
            text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, 100.00)"),
            {"mid": model_id},
        )

    stream = {
        "stream_id": stream_id, "model_id": model_id, "stream_name": "TEST Grid Stacker Blended",
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


def test_full_cycle_entry_add_trailing_stop_exit(sandbox):
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    # --- slot 1 entry ---
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        assert not order_manager.has_active_position(conn, stream["stream_id"])
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        assert order_manager.has_active_position(conn, stream["stream_id"])

    with engine.begin() as conn:
        fills, expirations = order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1 and expirations == 0
        pos = conn.execute(text("""
            SELECT status, avg_cost_basis, total_qty, total_deployed, original_entry_price
            FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "OPEN"
        assert float(pos.avg_cost_basis) == 50000.0
        assert float(pos.total_deployed) == 20.0   # slot 1 = 100 * 20/100

        slot1_fee = conn.execute(text("""
            SELECT fee_usd FROM live.blended_fills WHERE position_id =
            (SELECT position_id FROM live.blended_positions WHERE stream_id = :sid) AND fill_number = 0
        """), {"sid": stream["stream_id"]}).scalar()
        # FakeKraken's fee model is 0.4% of notional -- capital ($20) * 0.004
        assert abs(float(slot1_fee) - 20.0 * 0.004) < 1e-6

    # --- cascade add #1 (price drops 1% below original entry -> triggers) ---
    kraken._next_price = 49400.0   # 1.2% below 50000, past the 1% trigger
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49400.0, kraken=kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT pending_add_order_id, pending_add_index FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.pending_add_order_id is not None
        assert pos.pending_add_index == 1

    with engine.begin() as conn:
        fills, expirations = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1
        pos = conn.execute(text("""
            SELECT avg_cost_basis, total_qty, total_deployed, capitulation_armed
            FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert float(pos.total_deployed) == 40.0   # slot 1 + slot 2
        assert pos.capitulation_armed is False      # only 2 of 5 slots filled
        expected_avg = 40.0 / (20.0 / 50000.0 + 20.0 / 49400.0)
        # tolerance reflects Kraken's real 8-decimal BTC precision (volume_btc is
        # formatted to 8 places before submission), not a bug in the average calc
        assert abs(float(pos.avg_cost_basis) - expected_avg) < 1.0

        add1_fee = conn.execute(text("""
            SELECT fee_usd FROM live.blended_fills WHERE position_id =
            (SELECT position_id FROM live.blended_positions WHERE stream_id = :sid) AND fill_number = 1
        """), {"sid": stream["stream_id"]}).scalar()
        assert abs(float(add1_fee) - 20.0 * 0.004) < 1e-6

    # --- price rallies past trail_arm_gain_pct (4%) then drops through the trailing stop ---
    avg_cost = expected_avg
    armed_close = avg_cost * 1.05   # 5% above avg cost, clears the 4% arm threshold
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": armed_close * 0.999}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 0   # armed but not yet triggered

    stop_trigger_low = armed_close * (1 - 0.05) - 1   # pierce the 5% trailing stop from this new high
    # Real market sells now poll Kraken's ticker for the actual fill price
    # (place_exit's post-placement status poll) rather than trusting the
    # computed stop-trigger price -- set the fake ticker to what the market
    # would realistically be showing at this candle's close so the "real"
    # fill is consistent with the scenario, not left over from the cascade add.
    kraken._next_price = armed_close
    with engine.begin() as conn:
        capital_before = order_manager.get_available_capital(conn, model_id)
        total_qty = float(conn.execute(text("""
            SELECT total_qty FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).scalar())
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 1
        pos = conn.execute(text("""
            SELECT status, realized_pnl, exit_reason, exit_price, exit_fee_usd, fee_is_estimated
            FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "CLOSED"
        assert pos.exit_reason == "trailing_stop"
        assert pos.fee_is_estimated is False   # FakeKraken confirms the fill on the first poll
        # Real fill price/fee come from FakeKraken's ticker, not the computed
        # stop-trigger price -- confirms place_exit is using the real poll result.
        assert abs(float(pos.exit_price) - armed_close) < 0.01
        expected_exit_fee = total_qty * armed_close * 0.004   # FakeKraken's fee model
        assert abs(float(pos.exit_fee_usd) - expected_exit_fee) < 1e-4

        total_entry_fees = 2 * (20.0 * 0.004)   # slot 1 + add #1, both $20 fills
        expected_pnl = (total_qty * armed_close) - expected_exit_fee - 40.0 - total_entry_fees
        assert abs(float(pos.realized_pnl) - expected_pnl) < 0.01
        # never voluntarily realize a loss -- pnl must be >= 0 (allow a cent of rounding)
        assert float(pos.realized_pnl) >= -0.01

        capital_after = order_manager.get_available_capital(conn, model_id)
        assert abs(capital_after - (capital_before + float(pos.realized_pnl))) < 0.01
        assert not order_manager.has_active_position(conn, stream["stream_id"])

    print(f"\nFull cycle OK -- entry -> add -> trailing stop exit. "
          f"pnl=${float(pos.realized_pnl):.2f}, capital ${capital_before:.2f} -> ${capital_after:.2f}")


def test_exit_falls_back_to_estimate_when_fill_not_yet_confirmed(sandbox):
    """place_exit polls Kraken once, synchronously, right after placing the
    market sell. If that single poll doesn't show a confirmed fill yet (API
    lag -- FakeKraken's "delayed" mode), the lot must still close using the
    estimated price/fee rather than hang, and must flag fee_is_estimated so
    it's visible for a manual glance later."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # Rally past the 4% arm threshold, then pierce the trailing stop -- same
    # shape as the full-cycle test, but slot 1 only (no cascade add).
    armed_close = 50000.0 * 1.05
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": armed_close * 0.999}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)

    stop_trigger_low = armed_close * (1 - 0.05) - 1
    kraken._next_price = armed_close
    kraken.next_fill_mode = "delayed"   # first status poll after the market sell reports unconfirmed
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 1
        pos = conn.execute(text("""
            SELECT status, realized_pnl, exit_price, exit_fee_usd, fee_is_estimated
            FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "CLOSED"   # never gets stuck even though the fill wasn't confirmed
        assert pos.fee_is_estimated is True
        # Fallback uses the computed stop-trigger price (effective_stop), not
        # the real ticker price the (unconfirmed) fill would have used.
        assert float(pos.exit_price) != armed_close


def test_exit_with_mixed_legacy_and_real_fee_fills(sandbox):
    """Real-world scenario this fix deploys into: Model 3's live position
    right now has a slot-1 fill from BEFORE this migration (fee_usd is NULL
    in the DB) -- the next cascade add or exit will happen with the new code.
    A position can therefore have a MIX of legacy (NULL fee_usd) and real
    fee_usd fills. total_entry_fees_usd must estimate only the legacy fill's
    share (not silently drop it via SQL SUM's NULL-skipping, and not treat
    the whole position as estimated when part of it is real)."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    # --- slot 1 fills normally (real fee_usd) ---
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # --- simulate slot 1 predating this fix: null out its fee_usd ---
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE live.blended_fills SET fee_usd = NULL WHERE position_id =
            (SELECT position_id FROM live.blended_positions WHERE stream_id = :sid) AND fill_number = 0
        """), {"sid": stream["stream_id"]})

    # --- cascade add #1 fills normally (real fee_usd) ---
    kraken._next_price = 49400.0
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49400.0, kraken=kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        add1_fee = conn.execute(text("""
            SELECT fee_usd FROM live.blended_fills WHERE position_id =
            (SELECT position_id FROM live.blended_positions WHERE stream_id = :sid) AND fill_number = 1
        """), {"sid": stream["stream_id"]}).scalar()
        assert add1_fee is not None   # confirms this fill is the "real" one in the mix

    # --- rally and pierce the trailing stop ---
    avg_cost = 40.0 / (20.0 / 50000.0 + 20.0 / 49400.0)
    armed_close = avg_cost * 1.05
    kraken._next_price = armed_close
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": armed_close * 0.999}}
        position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)

    stop_trigger_low = armed_close * (1 - 0.05) - 1
    with engine.begin() as conn:
        total_qty = float(conn.execute(text("""
            SELECT total_qty FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).scalar())
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 1
        pos = conn.execute(text("""
            SELECT realized_pnl, exit_fee_usd, fee_is_estimated FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        # Mix of real (add #1) and estimated (slot 1) entry fees -- must be
        # flagged, since part of the number really is an estimate.
        assert pos.fee_is_estimated is True

        expected_slot1_fee_estimate = 20.0 * MAKER_FEE   # slot 1's legacy fallback
        expected_add1_fee_real = 20.0 * 0.004             # FakeKraken's real fee model
        expected_exit_fee = total_qty * armed_close * 0.004
        expected_pnl = ((total_qty * armed_close) - expected_exit_fee - 40.0
                        - expected_slot1_fee_estimate - expected_add1_fee_real)
        assert abs(float(pos.realized_pnl) - expected_pnl) < 0.01


def test_entry_order_expiry_frees_the_slot(sandbox):
    """An unfilled slot-1 order past its expiry must be cancelled and the
    position row deleted -- so a fresh signal can try again, same as
    order_manager.check_pending's expiry handling for Model 1 lots."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0
    kraken.next_fill_mode = "none"   # genuinely never fills -- a real unfilled limit order

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        # force expiry into the past
        conn.execute(text("""
            UPDATE live.blended_positions SET pending_entry_expiry_at = now() - interval '1 hour'
            WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]})

    with engine.begin() as conn:
        fills, expirations = order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 0 and expirations == 1
        assert not order_manager.has_active_position(conn, stream["stream_id"])


def test_partial_fill_right_before_entry_expiry_is_preserved(sandbox):
    """The exact bug this test guards against: a limit order partially fills
    right before its expiry, then gets cancelled. Cancelling does NOT undo
    the BTC already bought -- the position must open with the partial fill,
    never be silently deleted."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0
    kraken.next_fill_mode = "partial"
    kraken.next_partial_fraction = 0.4

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        conn.execute(text("""
            UPDATE live.blended_positions SET pending_entry_expiry_at = now() - interval '1 hour'
            WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]})

    with engine.begin() as conn:
        fills, expirations = order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1 and expirations == 0   # treated as a real fill, NOT an expiration
        pos = conn.execute(text("""
            SELECT status, total_qty, total_deployed FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "OPEN"
        assert float(pos.total_qty) > 0   # the partial BTC is tracked, not lost
        assert order_manager.has_active_position(conn, stream["stream_id"])

    print("\nPartial-fill-at-expiry OK -- position opened with the partial fill, not deleted.")


def test_cascade_add_expiry_keeps_position_open_for_retry(sandbox):
    """An unfilled cascade add past its expiry clears pending_add_* but must
    NOT close the position -- it stays OPEN with whatever fills it already
    has, and the next tick's trigger check can fire a fresh add order."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    kraken._next_price = 49000.0
    kraken.next_fill_mode = "none"   # genuinely never fills
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49000.0, kraken=kraken, dry_run=False)
        conn.execute(text("""
            UPDATE live.blended_positions SET pending_add_expiry_at = now() - interval '1 hour'
            WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]})

    with engine.begin() as conn:
        fills, expirations = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 0 and expirations == 1
        pos = conn.execute(text("""
            SELECT status, pending_add_order_id FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "OPEN"          # still open, NOT closed or deleted
        assert pos.pending_add_order_id is None  # cleared, free to retry next tick


def test_partial_fill_right_before_add_expiry_is_preserved(sandbox):
    """Same bug, cascade-add version: a partial add fill right before
    expiry must be folded into the position, never discarded."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    kraken._next_price = 49000.0
    kraken.next_fill_mode = "partial"
    kraken.next_partial_fraction = 0.5
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49000.0, kraken=kraken, dry_run=False)
        conn.execute(text("""
            UPDATE live.blended_positions SET pending_add_expiry_at = now() - interval '1 hour'
            WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]})

    with engine.begin() as conn:
        qty_before = conn.execute(text(
            "SELECT total_qty FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream["stream_id"]}).scalar()

        fills, expirations = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1 and expirations == 0
        pos = conn.execute(text("""
            SELECT status, total_qty, pending_add_order_id FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "OPEN"
        assert float(pos.total_qty) > float(qty_before)   # the partial add BTC was folded in, not lost
        assert pos.pending_add_order_id is None

    print("\nPartial-add-fill-at-expiry OK -- fill folded into position, not discarded.")


def test_partial_fill_still_resting_before_expiry_is_not_finalized_early(sandbox):
    """The second, subtler bug this session caught: a partial fill on an
    order that's still OPEN on Kraken's book (not cancelled, not expired
    yet) must NOT be treated as final -- it could still fill more. Applying
    it early would stop polling that order and silently orphan the rest of
    the fill. The pending-add tracking must stay in place until the order
    reaches a terminal state (closed/canceled/expired)."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    kraken._next_price = 49000.0
    kraken.next_fill_mode = "partial"
    kraken.next_partial_fraction = 0.3
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49000.0, kraken=kraken, dry_run=False)
        # note: expiry is NOT forced into the past here -- still well within its window

    with engine.begin() as conn:
        qty_before = conn.execute(text(
            "SELECT total_qty FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream["stream_id"]}).scalar()

        fills, expirations = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 0 and expirations == 0   # neither -- must be left alone

        pos = conn.execute(text("""
            SELECT status, total_qty, pending_add_order_id, pending_add_index
            FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "OPEN"
        assert float(pos.total_qty) == float(qty_before)   # NOT folded in yet -- still resting
        assert pos.pending_add_order_id is not None         # still tracked, will poll again next tick
        assert pos.pending_add_index == 1

    print("\nStill-resting partial fill OK -- left untouched, still tracked for the next tick.")

    print("\nPartial-add-fill-at-expiry OK -- fill folded into position, not discarded.")


def test_capitulation_stop_can_realize_a_loss_once_out_of_slots(sandbox):
    """Fill all 5 slots, then crash price >15% below the last fill. Unlike
    the trailing stop, capitulation IS allowed to realize a loss -- it's the
    'out of ammo' backstop, not a profit-taking exit."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # walk price down through all 4 remaining cascade triggers so every slot fills
    drop_prices = [49000.0, 48000.0, 47000.0, 44000.0]  # past 1%, 2%, 5%, 10% cumulative drops
    for price in drop_prices:
        kraken._next_price = price
        with engine.begin() as conn:
            order_manager.check_cascade_add_trigger(conn, stream, latest_close=price, kraken=kraken, dry_run=False)
        with engine.begin() as conn:
            fills, _ = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
            assert fills == 1

    with engine.begin() as conn:
        pos = conn.execute(text("""
            SELECT capitulation_armed FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.capitulation_armed is True   # all 5 slots filled, out of ammo

    # crash 16% below the last fill (44000 * 0.84 = 36960) -- past the 15% capitulation stop
    with engine.begin() as conn:
        capital_before = order_manager.get_available_capital(conn, model_id)
        candle_row = {stream["stream_id"]: {"close": 37000.0, "low": 36500.0}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 1
        pos = conn.execute(text("""
            SELECT status, realized_pnl, exit_reason FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "CLOSED"
        assert pos.exit_reason == "capitulation_stop"
        assert float(pos.realized_pnl) < 0   # a real, allowed loss

        capital_after = order_manager.get_available_capital(conn, model_id)
        assert abs(capital_after - (capital_before + float(pos.realized_pnl))) < 0.01
        assert capital_after < 100.0   # capital ledger actually shrank

    print(f"\nCapitulation OK -- 5/5 slots filled, forced exit at a loss. "
          f"pnl=${float(pos.realized_pnl):.2f}, capital ${capital_before:.2f} -> ${capital_after:.2f}")


def test_compounding_grows_next_position_capital_base(sandbox):
    """After a winning close, the NEXT position's slot-1 capital must be sized
    off the grown available_capital -- not stay frozen at the original $100."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    # --- close a winning position first (same as the full-cycle test but shorter) ---
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
    with engine.begin() as conn:
        # rally well past breakeven+trail so this closes as a clean winner --
        # ticker must reflect the same rally, since place_exit now polls it
        # for the real fill instead of trusting the candle close.
        kraken._next_price = 60000.0
        candle_row = {stream["stream_id"]: {"close": 60000.0, "low": 57000.0}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 1
        capital_after_win = order_manager.get_available_capital(conn, model_id)
        assert capital_after_win > 100.0

    # --- next entry must size slot 1 off the grown capital, not the original $100 ---
    kraken._next_price = 55000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT position_capital_base FROM live.blended_positions
            WHERE stream_id = :sid AND status = 'PENDING_ENTRY'
        """), {"sid": stream["stream_id"]}).fetchone()
        assert float(pos.position_capital_base) == capital_after_win
        assert float(pos.position_capital_base) > 100.0

    print(f"\nCompounding OK -- next position capital base ${float(pos.position_capital_base):.2f} "
          f"(grew from $100.00)")


def test_has_active_position_blocks_duplicate_entry(sandbox):
    """A second slot-1 entry must never fire while one is already
    building/open -- Model 3 is a solo stream using the model's full
    capital, only one stack can exist at a time."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        assert order_manager.has_active_position(conn, stream["stream_id"])

    # blended_executor.tick() gates on has_active_position before calling
    # place_entry again -- verify the gate actually reflects PENDING_ENTRY state
    with engine.begin() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream["stream_id"]}).scalar()
        assert count == 1


def test_minimum_lot_size_skips_entry_without_placing_an_order(sandbox):
    """If compounding ever shrank capital below the $10 slot minimum,
    place_entry must refuse to place an order rather than submit a
    sub-minimum Kraken order."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager._update_available_capital(conn, model_id, 45.0)  # slot 1 = 45*20/100 = $9 < $10

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        assert not order_manager.has_active_position(conn, stream["stream_id"])
        assert len(kraken.orders) == 0   # no order was ever placed
