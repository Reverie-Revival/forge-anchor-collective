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


def tilted_slot_weights(base_weights: list, fng_value, tilt_cfg: dict, slot_count: int,
                         trend_val: float = None, close: float = None) -> list:
    """
    Skew slot_capital_weight based on the Fear & Greed reading at the moment
    a position opens (locked in for that whole cascade, not re-evaluated per
    fill -- callers must compute this ONCE at slot-1 entry and freeze the
    result, not recall this per fill with a later fng_value).

    tilt_cfg:
      direction: +1 = front-load fear (bet bigger, earlier, on more extreme
                 fear readings); -1 = back-load fear (reserve capital for
                 later slots on more extreme fear readings, betting more pain
                 may be coming). Backtested: -1 (back-load) clearly wins.
      strength:  0 = no skew (falls back to base_weights); higher = more
                 aggressive skew. Backtested sweep across 0.2-0.8: 0.4 is the
                 real peak (0.3-0.4 the sweet spot) -- decays on both sides,
                 not "more is better."
      apply_to_slot1: if False (default), slot 1 stays at its base weight and
                 only slots 2..N are skewed relative to each other -- keeps
                 the entry itself simple/unconditional. If True, slot 1 is
                 included in the skew too.
      trend_sma_period: optional. If set, and the caller supplies trend_val
                 (that SMA's value at entry) and close, scales `strength` by
                 trend_strength_below/trend_strength_above depending on
                 whether close is below/above that SMA -- e.g. lean into the
                 tilt harder in a confirmed downtrend (more room to fall,
                 back-loading matters more) and dampen it in an uptrend (a
                 dip is more likely shallow).
      trend_strength_below / trend_strength_above: multipliers on `strength`,
                 default 1.0 each (no trend adjustment) if trend_sma_period
                 unset or trend_val/close not supplied.

    fng_value 50 (neutral) always reduces to base_weights regardless of
    strength; 0 (extreme fear) / 100 (extreme greed) are the max skew.
    """
    if not tilt_cfg or fng_value is None:
        return base_weights

    direction = tilt_cfg.get("direction", 1)
    strength  = tilt_cfg.get("strength", 0.4)
    apply_to_slot1 = tilt_cfg.get("apply_to_slot1", False)

    trend_period = tilt_cfg.get("trend_sma_period")
    if trend_period and trend_val is not None and close is not None:
        below = close < trend_val
        mult = tilt_cfg.get("trend_strength_below", 1.0) if below else tilt_cfg.get("trend_strength_above", 1.0)
        strength *= mult

    tilt = direction * (50 - fng_value) / 50.0  # + on fear, - on greed (direction can flip this)

    start_idx = 0 if apply_to_slot1 else 1
    n = slot_count - start_idx
    if n <= 0:
        return base_weights

    # symmetric ramp: the earliest affected slot gets the most positive skew,
    # the latest the most negative, centered on zero so total weight units
    # shift between slots rather than growing/shrinking outright.
    ramp = [(n - 1) / 2.0 - j for j in range(n)]

    # Floor: 0.5x a $20 base weight = $10, CLAUDE.md's stated minimum lot size
    # (Kraken order minimums + round-trip fee drag make anything smaller
    # impractical). Only exact for the $20/slot base this was tuned against --
    # a differently-sized base weight would need a different floor to hold the
    # same real $10 minimum.
    min_factor = tilt_cfg.get("min_factor", 0.5)

    new_weights = list(base_weights[:slot_count])
    for j, idx in enumerate(range(start_idx, slot_count)):
        factor = 1 + strength * tilt * ramp[j]
        factor = max(factor, min_factor)
        new_weights[idx] = base_weights[idx] * factor
    return new_weights
