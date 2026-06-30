# Model 1

**Status:** Locked — 3 streams validated, ready for deployment decision  
**Capital:** $100 (3 streams × $33.33/lot × 1 slot)  
**Architecture:** 3 streams, single-slot baseline (slot behavior to be refined pre-deployment)

## Purpose

Model 1 is the first live deployment of the Forge Anchor tournament architecture. Its primary goal is to validate the full system end-to-end — data pipeline, backtester, execution engine, and reporting — while generating real performance data to benchmark all future models against.

Being Model 1, it is as much a systems test as a strategy test. The results are data regardless of grade.

## Locked Streams

Three streams cover three distinct market regimes with zero signal overlap confirmed in testing:

| Stream | Type | Trail | Active In | Grade | stream_id |
|---|---|---|---|---|---|
| [Momentum Rider v1](../streams/momentum-rider-v1.md) | Trend-following | 5% | Bull market, F&G > 25, RSI > 55 | 4 — Strong | 1 |
| [Dip Hunter v1](../streams/dip-hunter-v1.md) | Fear bounce (mean reversion) | 7.5% | Extreme fear, F&G < 20, 25%+ drawdown | 4 — Strong | 2 |
| [Breakout Scout v1](../streams/breakout-scout-v1.md) | Consolidation breakout | 5% | Low-vol squeeze + bullish sentiment | 4 — Strong | 3 |

Surge Rider v1 and Steady Climber v1 were considered during design but never built or tested. Model 1 does not need 5 streams — 3 well-differentiated streams beat 5 redundant ones.

## Capital Allocation (v2 baseline)

| Stream | Lot Size | Slots | Stream Capital |
|---|---|---|---|
| Momentum Rider v1 | $33.33 | 1 | $33.33 |
| Dip Hunter v1 | $33.33 | 1 | $33.33 |
| Breakout Scout v1 | $33.33 | 1 | $33.33 |
| **Total** | | | **$99.99** |

Slot behavior (`slot_mode`) is currently `single` — baseline configuration. Before deployment, slot modes may be refined per stream (DH: scale_down to average; MR: scale_up to pyramid; BS: stays single).

## Validation Results (v2 baseline, Run #1)

| Window | Period | Ann. Return | Trades | Max DD | vs S&P |
|---|---|---|---|---|---|
| Primary | 2019–2023 | **+12.7%** | 157 | -21.2% | Beats (~10% avg) |
| Full History | 2018– | +5.6% | 264 | -23.4% | Below |
| Recent | 2024– | +3.6% | 66 | -19.6% | Below |
| 2026 YTD | 2026 | -16.2% | 15 | -8.6% | Above (BTC -54%) |

Primary window (2019–2023) is the primary gate: varied regimes (2019 grind, 2020 crash+bull, 2021 peak, 2022 bear, 2023 recovery). +12.7% Grade 4 — Strong.

## Constraints

- BTC/USD only
- No leverage
- Limit orders only (0.25% maker fee, 0.50% round trip)
- Trailing stops only — no fixed targets
- Long-only (no shorting)

## Deployment Gate

- Primary window annualized return > 10% ✓
- All 3 streams validated with complementary regimes ✓
- Model-level test complete across 4 windows ✓
- Slot behavior finalized (pending) — current single-slot baseline is deployable

## Benchmarks

Model 1 performance compared against:
- S&P 500 return for the same period
- BTC buy-and-hold for the same period
- Cash (doing nothing)

Since there are no prior models, there is no head-to-head comparison for Model 1. All future models compare against it.

## Grading Criteria

| Grade | Label | Threshold |
|---|---|---|
| 5 | Elite | 20%+ annualized, sustained 2+ years |
| 4 | Strong | 10–19% annualized |
| 3 | Passing | 8–12% annualized |
| 2 | Weak | Positive but below 8% |
| 1 | Poor | Break-even or loss |

## Model Commitment Rule

Once deployed, Model 1 runs for the full duration of its experiment regardless of performance. It stops only if capital is exhausted or there is a critical system failure. The long-term data is worth more than the $100.
