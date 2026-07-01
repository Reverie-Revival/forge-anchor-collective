# Model 1

**Status:** Locked — all 3 streams at v2, ready for deployment decision  
**Capital:** $100 (3 streams × $33.33/lot × 1 slot)  
**Architecture:** 3 streams, single-slot baseline

## Purpose

Model 1 is the first live deployment of the Forge Anchor tournament architecture. Its primary goal is to validate the full system end-to-end — data pipeline, backtester, execution engine, and reporting — while generating real performance data to benchmark all future models against.

Being Model 1, it is as much a systems test as a strategy test. The results are data regardless of grade.

## Locked Streams

Three streams cover three distinct market regimes with zero signal overlap:

| Stream | Type | Timeframe | Trail | Active In | stream_id |
|---|---|---|---|---|---|
| [Momentum Rider v2](../streams/momentum-rider-v1.md) | Trend-following | 4h | 7% | Bull market, EMA momentum | 1 |
| [Dip Hunter v2](../streams/dip-hunter-v1.md) | Fear bounce | 1h | 10% | Extreme fear, F&G ≤ 20, 25%+ drawdown | 2 |
| [Breakout Scout v2](../streams/breakout-scout-v2.md) | Consolidation breakout | 1h | 10% | Greedy sentiment, SMA 200 above, squeeze | 3 |

Surge Rider v1 and Steady Climber v1 were considered during design but never built or tested. Model 1 does not need 5 streams — 3 well-differentiated streams beat 5 redundant ones.

## Capital Allocation

| Stream | Lot Size | Slots | Stream Capital |
|---|---|---|---|
| Momentum Rider v2 | $33.33 | 1 | $33.33 |
| Dip Hunter v2 | $33.33 | 1 | $33.33 |
| Breakout Scout v2 | $33.33 | 1 | $33.33 |
| **Total** | | | **$99.99** |

Allocation tested: weighting MR heavier (up to $60/$25/$15) increases PV2 returns by +2–3% but costs 2026 YTD performance significantly (DH is the active stream in the current fear regime). Equal allocation preserves regime responsiveness.

## Model-Level Backtest Results

### Run #4 — MR v2 + DH v2 + BS v2 (current, locked)

| Window | Period | Ann. Return | Trades | Max DD |
|---|---|---|---|---|
| Primary v2 | 2022–now | **+15.3%** | 66 | -12.8% |
| Full History | 2018–now | **+22.2%** | 148 | -15.8% |
| Recent | 2024–now | **+14.7%** | 40 | -13.6% |
| 2026 YTD | 2026–now | **+16.4%** | 8 | -2.8% |
| Primary Window | 2019–2023 | **+30.7%** | 80 | -13.9% |

### Run History (all use equal $33.33 allocation unless noted)

| Run | Streams | PV2 | Full History | Recent | 2026 YTD |
|---|---|---|---|---|---|
| #1 | MR v1 + DH v1 + BS v1 | — | +5.6% | +3.6% | -16.2% |
| #2 | MR v2 + DH v1 + BS v1 | +8.5% | +16.0% | +9.1% | -6.6% |
| #3 | MR v2 + DH v2 + BS v1 | +11.9% | +17.0% | +9.6% | +16.4% |
| **#4** | **MR v2 + DH v2 + BS v2** | **+15.3%** | **+22.2%** | **+14.7%** | **+16.4%** |

Each stream upgrade contributed meaningfully. MR v2 was the largest single lift (+8.5% → baseline). DH v2 fixed the 2026 YTD (-6.6% → +16.4%). BS v2 lifted the floor across all windows and compressed model DD.

## Individual Stream Results (locked configs, $10 lot × 1 slot)

| Stream | Primary v2 | Full History | Recent | 2026 YTD |
|---|---|---|---|---|
| Momentum Rider v2 | +21.5% | +25.9% | +16.9% | — |
| Dip Hunter v2 | +11.7% | +12.1% | +10.5% | +53.0% |
| Breakout Scout v2 | +11.6% | +25.1% | +16.6% | 0 trades |

## Constraints

- BTC/USD only
- No leverage
- Limit orders only (0.25% maker fee, 0.50% round trip)
- Trailing stops only — no fixed targets
- Long-only (no shorting)

## Deployment Gate

- [x] All 3 streams tuned and locked at v2
- [x] Model-level backtest Run #4 complete across 5 windows
- [x] Primary v2 annualized return +15.3% — beats S&P 500 target (10%)
- [x] All windows positive
- [x] Equal $33.33 allocation confirmed vs weighted alternatives
- [ ] Deployment decision — pending

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

## Future Slot Design (pre-deployment)

Current slot mode is `single` across all streams. A staggered-slot redesign is planned before deployment:
- Two independent capital buckets per stream
- Slot 2 cannot enter the same signal as Slot 1 — must wait for the next signal
- Allows catching consecutive signals when one slot is already occupied
- Best fit for DH (sequential fear dips) and BS (consecutive squeeze breakouts)
- MR less likely to benefit (sustained trends don't repeat entry signals quickly)
