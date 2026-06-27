# ADR 004 — Model Tournament Architecture

**Date:** 2026-06-26
**Status:** Accepted

## Decision
The system is built around versioned, independently-capitalized models that run in parallel. Each model is a complete trading system: 5 strategy streams × 2 position slots = $100 total capital. New models are built, backtested, and deployed alongside prior ones every 2-3 months.

## What a Model Is
- 5 strategy streams, each with 2 position slots ($10 per slot)
- Each stream has a unique descriptive name and a version number
- A model is identified by its model version (e.g., Model 1, Model 2)
- $100 of independent capital per model

## Stream Naming Convention
Streams are named descriptively to reflect their personality, plus a version number:
- Format: `[Descriptive Name] v[N]` — e.g., "Momentum Rider v1", "Dip Hunter v2"
- **Carried forward unchanged** → same name, same version number
- **Adjusted from prior model** → same name, incremented version (v1 → v2)
- **Entirely new stream type** → new descriptive name at v1

This makes it easy to trace which stream lineage performed well across models.

## Model Lifecycle
1. **Build** — design 5 streams, set parameters, backtest across historical data
2. **Gate** — backtest must show confidence across diverse market conditions (no calendar gate)
3. **Deploy** — $100 live on Kraken, runs independently alongside prior models
4. **Overlap** — while current model runs live, next model is being designed and backtested
5. **Repeat** — every 2-3 months

## No Mandatory Paper Trading
$100 live capital is low enough that live deployment serves as the real-world validation. Paper trading is optional, not required. Backtest confidence is the gate to deployment, not a calendar waiting period.

## Grading System
| Grade | Criteria |
|---|---|
| Passing | Consistently beats S&P 500 for the same period |
| Strong | 20%+ annualized return, consistently |
| Elite | Strong grade held for 2+ consecutive years |

Elite models are candidates for increased capital allocation. No model receives more capital based on short-term results alone.

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
