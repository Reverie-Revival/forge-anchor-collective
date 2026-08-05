"""
Layer 2b — Model 4's four mechanisms, live-code integration tests: sentiment
tilt, slot promotion, capitulation ladder, shallow breakeven margin.

None of these existed anywhere in the live blended stack until this file was
added alongside them -- Model 3's tests/live/test_blended_state_machine.py
only ever exercised the plain capitulation_stop_pct/trailing-stop mechanics,
since Model 3's own config never uses any of the four. This is the real,
non-dry-run integration coverage for the parts that make Model 4 actually
behave like Model 4 rather than a copy of Model 3.

Run:
    pytest tests/live/test_model4_mechanics.py -v -s
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import blended_order_manager as order_manager
from src.live import blended_position_monitor as position_monitor
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 995

BASE_PARAMS = {
    "slots": {"slot_capital_weight": [20, 20, 20, 20, 20]},
    "filters": {}, "sentiment": {"fear_greed": {}},
    "position": {
        "compound": True,
        "trailing_stop_pct": 5.0, "trail_arm_gain_pct": 4,
        "cumulative_drop_pcts": [1, 2, 5, 10], "entry_expiry_candles": 2,
        "capitulation_ladder_pcts": [20, 22, 24, 26, 28],
        "capitulation_ladder_final_cut_pct": 30,
        "sentiment_tilt": {"direction": -1, "strength": 0.4},
        "slot_promotion_days": [3, 6, 9, 12], "max_promotions_per_position": 1,
        "shallow_breakeven_margin_pct": 1.0, "shallow_slot_threshold": 5,
    },
    "core_params": {"dip_pct": 1.0}, "core_signal": "fear_dip", "primary_timeframe": "4h",
}


@pytest.fixture
def sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- Model 4 mechanics', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'TEST GS: Reflex', 'v2', 'blended_dca', CAST(:p AS jsonb), 5, 'blended', 20.0)
            RETURNING stream_id
        """), {"mid": model_id, "p": json.dumps(BASE_PARAMS)}).scalar()
        conn.execute(text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, 100.00)"),
                     {"mid": model_id})

    stream = {
        "stream_id": stream_id, "model_id": model_id, "stream_name": "TEST GS: Reflex",
        "parameters": BASE_PARAMS, "slot_count": 5, "slot_mode": "blended", "lot_size_usd": 20.0,
    }

    yield engine, stream, model_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sentiment_data WHERE date = CURRENT_DATE"))
        conn.execute(text("""
            DELETE FROM live.blended_fills WHERE position_id IN
            (SELECT position_id FROM live.blended_positions WHERE model_id = :mid)
        """), {"mid": model_id})
        conn.execute(text("DELETE FROM live.blended_positions WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.blended_capital WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def _seed_fng(engine, value: int):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sentiment_data WHERE date = CURRENT_DATE"))
        conn.execute(text("""
            INSERT INTO sentiment_data (date, fng_value, fng_label) VALUES (CURRENT_DATE, :v, 'test')
        """), {"v": value})


def test_sentiment_tilt_skews_and_freezes_slot_capitals(sandbox):
    """direction=-1 (back-load fear): at extreme fear (fng=0), slot 2 should
    be skewed BELOW its base $20 weight and slot 5 ABOVE it -- and a later
    cascade add must reuse this exact frozen split, not recompute a fresh
    tilt against a different fng_value."""
    engine, stream, model_id = sandbox
    _seed_fng(engine, 0)   # extreme fear
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT frozen_slot_capitals FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        frozen = pos.frozen_slot_capitals
        assert frozen is not None, "sentiment_tilt was configured but frozen_slot_capitals is NULL"

    # Hand-computed expected WEIGHTS (not dollars yet): base [20,20,20,20,20],
    # direction=-1, strength=0.4, fng=0 -> tilt = -1*(50-0)/50 = -1.0.
    # apply_to_slot1=False (default) -> slot 1 untouched at $20. n=4,
    # ramp=[1.5,0.5,-0.5,-1.5]. factor_j = 1 + 0.4*(-1.0)*ramp_j = 1 - 0.4*ramp_j.
    # slot_capitals_for then NORMALIZES these weights to sum to $100 (the pool
    # size) -- they don't already sum to 100 on their own, so the actual
    # dollar split is these weights scaled by 100/sum(weights).
    weights = [20.0]
    for ramp in (1.5, 0.5, -0.5, -1.5):
        factor = max(1 - 0.4 * ramp, 0.5)
        weights.append(20.0 * factor)
    total_w = sum(weights)
    expected = [100.0 * w / total_w for w in weights]
    for got, exp in zip(frozen, expected):
        assert abs(got - exp) < 1e-9, f"frozen split {frozen} != expected {expected}"
    assert sum(frozen) - 100.0 < 1e-9, "frozen split must still sum to the full $100 pool"
    assert frozen[1] < 20.0 < frozen[4], "back-load fear should shrink slot 2, grow slot 5"

    # --- now change today's fng reading and confirm a cascade add does NOT re-tilt ---
    _seed_fng(engine, 90)   # would tilt the OPPOSITE direction if re-evaluated
    with engine.begin() as conn:
        fills, _ = order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1

    kraken._next_price = 49400.0  # past the 1% cascade trigger
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=49400.0, kraken=kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        fill = conn.execute(text("""
            SELECT capital FROM live.blended_fills WHERE position_id =
            (SELECT position_id FROM live.blended_positions WHERE stream_id = :sid) AND fill_number = 1
        """), {"sid": stream["stream_id"]}).scalar()
        assert abs(float(fill) - expected[1]) < 0.005, (   # live.blended_fills.capital is NUMERIC(12,2)
            f"cascade add used ${fill}, expected the FROZEN slot-2 capital ${expected[1]} "
            "(re-tilted against today's fng instead of reusing the frozen split)"
        )


def test_slot_promotion_fires_easier_trigger_after_days_open(sandbox):
    """slot_promotion_days=[3,...]: once the position has been open >= 3 days
    without slot 2's normal 1%-drop trigger firing, slot 2 should fire on
    the PRIOR slot's threshold (0% -- i.e. any price at or below the
    original entry) instead of needing a real 1% dip."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    # Back-date opened_at so days_open clears the 3-day promotion threshold --
    # simulates time passing without touching the real clock.
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE live.blended_positions SET opened_at = :ts WHERE stream_id = :sid
        """), {"ts": datetime.now(timezone.utc) - timedelta(days=4), "sid": stream["stream_id"]})

    # Price sits at exactly the ORIGINAL entry (0% drop) -- would NOT fire
    # the normal 1% trigger, but SHOULD fire the promoted 0% one.
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=50000.0, kraken=kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT pending_add_order_id, promotions_used FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.pending_add_order_id is not None, "promoted trigger should have fired at 0% drop"
        assert pos.promotions_used == 1


def test_slot_promotion_does_not_fire_before_days_threshold(sandbox):
    """Sanity check for the test above: with opened_at left at 'just now',
    the same 0%-drop price must NOT trigger the add (only the real 1% dip
    should, confirming the promotion path isn't just always-on)."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, stream, latest_close=50000.0, kraken=kraken, dry_run=False)
        pos = conn.execute(text("""
            SELECT pending_add_order_id, promotions_used FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.pending_add_order_id is None, "should not promote before the days threshold is crossed"
        assert pos.promotions_used == 0


def _fill_all_five_slots(engine, stream, kraken):
    """Drives entry + all 4 cascade adds to fully fill the position, returns
    the position_id. Trigger prices: 1/2/5/10% below the $50000 entry."""
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)

    for drop_pct, price in [(1, 49400.0), (2, 48900.0), (5, 47400.0), (10, 44900.0)]:
        kraken._next_price = price
        with engine.begin() as conn:
            order_manager.check_cascade_add_trigger(conn, stream, latest_close=price, kraken=kraken, dry_run=False)
        with engine.begin() as conn:
            fills, _ = order_manager.check_pending_add(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
            assert fills == 1, f"cascade add at {drop_pct}% drop did not fill"

    with engine.begin() as conn:
        pos = conn.execute(text("""
            SELECT position_id, capitulation_armed FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.capitulation_armed is True
        return pos.position_id


def test_capitulation_ladder_marks_down_then_final_cut_fires(sandbox):
    """Once all 5 slots fill (original_entry_price=$50000), ladder rungs sit
    at 20/22/24/26/28% below that ($40000/$39000/$38000/$37000/$36000), with
    a real unconditional exit at 30% ($35000). Crossing rungs one at a time
    should mark down the oldest slot's synthetic capital each time (visible
    as marked_count incrementing); crossing the final cut should force a
    real exit tagged capitulation_ladder_cut.

    trail_arm_gain_pct is set unreachably high here so the ordinary trailing
    stop/breakeven floor can never arm and confound the result -- confirmed
    directly against the backtest engine that a real (not test-isolated)
    config in this exact crash-straight-through-all-5-slots scenario arms
    the ordinary trail almost immediately (avg cost sits well below the
    stale slot-1 high the moment a hard cascade fills), which is correct,
    Gauntlet-validated behavior but would exit via trailing_stop before ever
    reaching the ladder's own rungs -- not what this test is isolating."""
    engine, stream, model_id = sandbox
    stream = dict(stream)
    stream["parameters"] = json.loads(json.dumps(stream["parameters"]))
    stream["parameters"]["position"]["trail_arm_gain_pct"] = 10_000
    kraken = FakeKraken()
    _fill_all_five_slots(engine, stream, kraken)

    # Drop through rung 1 (20% -> $40000) but not rung 2 (22% -> $39000).
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 40500.0, "low": 39900.0}}
        stops = position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        assert stops == 0
        pos = conn.execute(text("""
            SELECT marked_count, marked_capitals FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.marked_count == 1, f"expected 1 slot marked after crossing rung 1, got {pos.marked_count}"

    # Drop through rungs 2 and 3 in one candle ($39000 and $38000 both crossed).
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 38200.0, "low": 37900.0}}
        stops = position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        assert stops == 0
        pos = conn.execute(text("""
            SELECT marked_count FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.marked_count == 3, f"expected 3 slots marked after crossing rungs 2+3, got {pos.marked_count}"

    # Drop through the real, unconditional final cut (30% -> $35000).
    kraken._next_price = 34900.0
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": 34900.0, "low": 34800.0}}
        stops = position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        assert stops == 1
        pos = conn.execute(text("""
            SELECT status, exit_reason FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert pos.status == "CLOSED"
        assert pos.exit_reason == "capitulation_ladder_cut"


def test_shallow_breakeven_margin_converts_flat_exit_to_small_gain(sandbox):
    """Without shallow_breakeven_margin_pct, a position that arms (crosses
    +4%) then reverses before the 5% trail overtakes breakeven as the
    tighter constraint closes at EXACTLY $0 pnl by construction. With the
    1% margin (this stream's real config), the same scenario should close
    at a small REAL gain instead."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        avg_cost = float(conn.execute(text("""
            SELECT avg_cost_basis FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).scalar())

    # Rally just past the 4% arm level, then let the trailing stop's own 5%
    # pullback fire before price ever gets 6%+ above cost (the dead zone
    # where breakeven, not the trail, is the binding constraint).
    peak_close = avg_cost * 1.045
    kraken._next_price = peak_close
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": peak_close, "low": peak_close * 0.999}}
        stops = position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        assert stops == 0   # armed, not yet triggered

    # Breakeven-with-margin floor now exceeds the 5%-trail level -- pierce it.
    breakeven_with_margin = (avg_cost / (1 - 0.008)) * 1.01
    trail_stop = peak_close * 0.95
    assert breakeven_with_margin > trail_stop, "test setup assumption: breakeven+margin should be the binding constraint"
    kraken._next_price = breakeven_with_margin
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": breakeven_with_margin, "low": breakeven_with_margin - 1}}
        stops = position_monitor.check_all(conn, {stream["stream_id"]: stream}, candle_row, {"4h"}, kraken, dry_run=False)
        # Armed exits are now a real resting limit order, not an immediate
        # market sell -- check_all only places/re-prices it here.
        assert stops == 0
    with engine.begin() as conn:
        fills, _ = order_manager.check_pending_exit(conn, kraken, {stream["stream_id"]: stream}, dry_run=False)
        assert fills == 1
        pos = conn.execute(text("""
            SELECT realized_pnl FROM live.blended_positions WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert float(pos.realized_pnl) > 0.05, (
            f"expected a real guaranteed gain from the 1% margin, got pnl={pos.realized_pnl} "
            "(exactly ~$0 would mean the margin isn't being applied)"
        )
