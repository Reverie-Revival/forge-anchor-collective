# ADR 004 — Model Tournament Architecture

**Date:** 2026-06-26
**Status:** Accepted

## Decision
The system is built around versioned, independently-capitalized models that run in parallel. Each model is a complete trading system with 3–5 strategy streams and $100 total capital, allocated across streams at model assembly time. New models are built, backtested, and deployed alongside prior ones every 2-3 months.

## What a Model Is
- 3–5 strategy streams, each with its own lot size and slot count (minimum $10/slot)
- Capital allocation is configurable per stream: `lot_size_usd × slot_count` per stream, summing to $100
- Each stream has a unique descriptive name and a version number
- A model is identified by its model version (e.g., Model 1, Model 2)
- $100 of independent capital per model

## Model 1 Example Allocation
3 streams × $33.33/slot × 1 slot = $99.99 total:
- Momentum Rider v2: $33.33/slot × 1 slot
- Dip Hunter v2: $33.33/slot × 1 slot
- Breakout Scout v2: $33.33/slot × 1 slot

## Stream Naming Convention
Streams are named descriptively to reflect their personality, plus a version number:
- Format: `[Descriptive Name] v[N]` — e.g., "Momentum Rider v1", "Dip Hunter v2"
- **Carried forward unchanged** → same name, same version number
- **Adjusted from prior model** → same name, incremented version (v1 → v2)
- **Entirely new stream type** → new descriptive name at v1

This makes it easy to trace which stream lineage performed well across models.

## Model Lifecycle
1. **Build** — design 3–5 streams, set parameters, backtest across historical data
2. **Gate** — backtest must show confidence across diverse market conditions (no calendar gate)
3. **Deploy** — $100 live on Kraken, runs independently alongside prior models
4. **Overlap** — while current model runs live, next model is being designed and backtested
5. **Repeat** — every 2-3 months

## No Mandatory Paper Trading
$100 live capital is low enough that live deployment serves as the real-world validation. Paper trading is optional, not required. Backtest confidence is the gate to deployment, not a calendar waiting period.

## Grading System
S&P 500 historical average (~10% annualized) is the midpoint. Every model receives a grade regardless of outcome — poor results are data, not failure.

| Grade | Label | Criteria |
|---|---|---|
| 5 | Elite | 20%+ annualized, sustained 2+ years |
| 4 | Strong | Consistently beats S&P (10-19%) |
| 3 | Passing | Roughly matches S&P (8-12%) |
| 2 | Weak | Positive return but below S&P |
| 1 | Poor | Break-even or loss |

Grade 5 (Elite) models sustained for 2+ years are candidates for increased capital allocation. No model receives more capital based on short-term results alone.

## Model Commitment Rule
Every deployed model runs for the full duration of its experiment, regardless of performance. A model that underperforms is not shut down just because a newer model is doing better — the long-term data is more valuable than the $100. Models are only stopped if capital is fully exhausted or there is a critical system failure.

The goal is not to maximize returns on any single $100. The goal is to prove out a model architecture that could eventually work at scale. Cutting a model short corrupts the data.

## Why This Approach
- Parallel models create a natural controlled experiment — same market, different strategies
- Real performance data accumulates across model versions over time
- 3-year horizon produces enough data to make genuinely informed decisions
- Iteration every 2-3 months keeps the system improving without constant rebuilding
- Independent capital means a bad model can't drag down a good one

## Consequences
- `model_version` is a first-class field in every database table
- The analytics layer must support head-to-head model comparison
- Stream names and versions must be tracked in the `streams` table
- CLAUDE.md and all docs reference model numbers, not phase numbers
