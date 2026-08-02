"""
Pure position-sizing math shared between the backtester and live execution.

Extracted so live code can be provably running the exact formula that was
backtested, instead of a hand-ported copy kept in sync by comment only.
"""


def slot_capitals_for(capital_base: float, weights: list, slot_count: int) -> list:
    """
    Split capital_base across slot_count slots.

    weights: optional list of relative weights (e.g. [20, 20, 20, 20, 20] for
    equal split) -- if fewer than slot_count entries, falls back to an even
    split. Used by blended-mode positions to freeze a per-position slot split
    at the moment the position opens.
    """
    if weights and len(weights) >= slot_count:
        total_w = sum(weights[:slot_count])
        return [capital_base * w / total_w for w in weights[:slot_count]]
    return [capital_base / slot_count] * slot_count
