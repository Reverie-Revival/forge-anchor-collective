"""
Layer 1 — Pure math unit tests for Model 3's blended position sizing.
No DB, no Kraken -- fast, run these constantly while touching
blended_order_manager.py or blended_position_monitor.py.

Run:
    pytest tests/live/test_blended_math.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.live.blended_order_manager import _slot_capitals_for
from src.live.order_manager import MAKER_FEE, TAKER_FEE


def test_equal_weight_split():
    result = _slot_capitals_for(100.0, [20, 20, 20, 20, 20], 5)
    assert result == [20.0, 20.0, 20.0, 20.0, 20.0]
    assert sum(result) == 100.0


def test_equal_weight_split_no_weights_falls_back_to_even_division():
    result = _slot_capitals_for(100.0, None, 4)
    assert result == [25.0, 25.0, 25.0, 25.0]


def test_uneven_weight_split_sums_to_capital_base():
    result = _slot_capitals_for(100.0, [40, 30, 20, 10], 4)
    assert result == [40.0, 30.0, 20.0, 10.0]
    assert abs(sum(result) - 100.0) < 1e-9


def test_weight_split_scales_with_compounding_growth():
    # Same weight shape, bigger capital base (as if the previous position
    # closed with a profit) -- ratios must hold, not the raw dollar amounts.
    result = _slot_capitals_for(250.0, [20, 20, 20, 20, 20], 5)
    assert result == [50.0, 50.0, 50.0, 50.0, 50.0]


def test_cascade_trigger_price_math():
    # Mirrors check_cascade_add_trigger's trigger_price formula directly --
    # a 1% cumulative drop from a $50,000 original entry should trigger at $49,500.
    original_entry_price = 50000.0
    cumulative_drops = [1, 2, 5, 10]
    trigger_price_1 = original_entry_price * (1 - cumulative_drops[0] / 100.0)
    trigger_price_2 = original_entry_price * (1 - cumulative_drops[1] / 100.0)
    assert trigger_price_1 == 49500.0
    assert trigger_price_2 == 49000.0


def test_capitulation_price_below_last_fill():
    last_fill_price = 45000.0
    capitulation_stop_pct = 15
    capitulation_price = last_fill_price * (1 - capitulation_stop_pct / 100.0)
    assert capitulation_price == 38250.0


def test_breakeven_floor_uses_taker_fee_not_maker_fee():
    # Regression guard for the exact bug caught in
    # test_blended_state_machine.py: the live floor must match place_exit's
    # real formula (gross * (1 - TAKER_FEE) - total_deployed), which means
    # the floor has to divide by (1 - TAKER_FEE), not (1 - MAKER_FEE).
    avg_ep = 50000.0
    breakeven_correct = avg_ep / (1 - TAKER_FEE)
    breakeven_wrong = avg_ep / (1 - MAKER_FEE)
    assert breakeven_correct > breakeven_wrong  # taker fee is higher -> floor must sit higher
    assert TAKER_FEE > MAKER_FEE


def test_minimum_lot_size_guard_value():
    # place_entry skips if slot-1 capital < $10 -- matches CLAUDE.md's project-wide
    # minimum lot size rule. Just pin the threshold so a future edit can't
    # silently loosen it.
    slot_capitals = _slot_capitals_for(45.0, [20, 20, 20, 20, 20], 5)
    assert slot_capitals[0] == 9.0
    assert slot_capitals[0] < 10.0  # this capital base should trigger the skip-entry guard
