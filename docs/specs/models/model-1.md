# Model 1

**Status:** Design phase — streams under selection  
**Capital:** $100 (10 slots × $10)  
**Architecture:** 5 streams × 2 slots each  

## Purpose

Model 1 is the first live deployment of the Forge Anchor tournament architecture. Its primary goal is to validate the full system end-to-end — data pipeline, backtester, execution engine, and reporting — while generating real performance data to benchmark all future models against.

Being Model 1, it is as much a systems test as a strategy test. The results are data regardless of grade.

## Stream Selection

Five streams are chosen for diversity across market conditions — no two streams should depend on the same signal type. The goal is that in any given market regime (trending, ranging, volatile, quiet), at least some streams are active.

### Candidate Streams

| Stream | Type | Trail | Active In |
|---|---|---|---|
| [Momentum Rider v1](../streams/momentum-rider-v1.md) | Trend-following | 3% | Uptrending markets |
| [Dip Hunter v1](../streams/dip-hunter-v1.md) | Mean reversion | 2.5% | Any market with dips |
| [Breakout Scout v1](../streams/breakout-scout-v1.md) | Consolidation breakout | 4% | Post-consolidation moves |
| [Surge Rider v1](../streams/surge-rider-v1.md) | Volume momentum | 2% | High-participation moves |
| [Steady Climber v1](../streams/steady-climber-v1.md) | Trend-filtered pullback | 3.5% | Established uptrends only |

> Final 5 selections are confirmed after backtesting. Additional candidates may be added before lock-in.

## Capital Allocation

| Slot | Stream | Capital |
|---|---|---|
| Slot 1 | TBD | $10 |
| Slot 2 | TBD | $10 |
| Slot 3 | TBD | $10 |
| Slot 4 | TBD | $10 |
| Slot 5 | TBD | $10 |
| Slot 6 | TBD | $10 |
| Slot 7 | TBD | $10 |
| Slot 8 | TBD | $10 |
| Slot 9 | TBD | $10 |
| Slot 10 | TBD | $10 |

Each stream runs 2 slots independently — same strategy, same parameters, different capital pools. Slots compound individually: closing capital becomes the next lot's opening capital.

## Constraints

- BTC/USD only
- No leverage
- Limit orders only (0.25% maker fee, 0.50% round trip)
- Trailing stops only — no fixed targets
- Long-only (no shorting)

## Testing Plan

1. **Historical backtest** — full dataset Jan 2017 → present across all 5 streams
2. **Paper test** — live price feed, simulated execution, configurable start date
3. **Live deployment gate** — backtest confidence is the trigger, not calendar time

Multiple paper tests may run in parallel. The one selected for deployment is recorded in `backtest.model_tests.selected_for_deployment` and linked from `live.models.based_on_model_test_id`.

## Benchmarks

Model 1 performance will be compared against:
- S&P 500 return for the same period
- BTC buy-and-hold for the same period
- Cash (doing nothing)

Since there are no prior models, there is no head-to-head comparison for Model 1. All future models will compare against it.

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
