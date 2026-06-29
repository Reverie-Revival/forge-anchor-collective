# Forge Anchor Collective

Autonomous BTC trading system that converts market volatility into realized cash returns.
BTC is the vehicle, cash growth is the product. Goal: beat S&P 500 annualized returns (~10%)
with zero human intervention.

## Architecture

Each **Model** deploys with $100 total capital, split across 3–5 strategy streams.
Capital allocation is configurable per stream: each stream has its own `lot_size_usd` ($ per position) and `slot_count` (max concurrent positions).
Total model capital = Σ(lot_size_usd × slot_count) across all streams.
Default starting point: $10/lot × 2 slots per stream — but high-conviction streams can be weighted heavier.
**Minimum lot size: $10 per slot.** Positions below $10 are impractical given Kraken's minimum order sizes and the 0.50% round-trip fee eating a disproportionate share of a small position.
Models are versioned, deployed independently, and run in parallel with separate capital.

### Streams
Each stream has a unique descriptive name and a version number (e.g., "Momentum Rider v1").
When a stream carries forward unchanged into a new model, it keeps the same version.
When adjusted, the version increments. When replaced entirely, it gets a new name at v1.

### Exit method
Trailing stops (percentages, not fixed dollar amounts)

### AI role
Strategist/designer — deterministic code executes trades, not an LLM

## Model Tournament

- Every 2-3 months, a new model is built and backtested
- If it matches or beats the prior model in backtesting, it's deployed with its own $100
- All models run in parallel with independent capital
- Trades, performance, and results are always tagged to a model version
- Models are never retired — they run until capital is exhausted or manually stopped

### Grading System
S&P 500 (~10%) is the midpoint. Every model gets graded — poor results are data, not failure.

| Grade | Label | Criteria |
|---|---|---|
| 5 | Elite | 20%+ annualized, sustained 2+ years → candidate for more capital |
| 4 | Strong | Consistently beats S&P (10-19%) |
| 3 | Passing | Roughly matches S&P (8-12%) |
| 2 | Weak | Positive but below S&P |
| 1 | Poor | Break-even or loss |

### Model Commitment Rule
Every deployed model runs for the full duration of its experiment regardless of performance. The long-term data is worth more than the $100. Models stop only if capital is exhausted or there is a critical system failure.

## Model Lifecycle (what "phases" actually mean)

- **Build phase** — design streams, backtest across historical data, tune parameters
- **Deploy phase** — $100 live on Kraken, runs indefinitely
- **Overlap phase** — while current model runs live, next model is being built and backtested
- **Gate to deploy** — backtest confidence, not calendar time

There is no mandatory paper trading phase. $100 is low enough that live deployment IS the real-world test.

### Stream locking vs. model finalization

These are two distinct steps:

1. **Lock a stream** — the stream's signal is validated in isolation. It's approved as a candidate for this model. `backtest.streams` row is written. Allocation (lot_size_usd, slot_count) gets a default placeholder.
2. **Finalize a model** — all candidate streams assembled together, allocation weights set per stream, then model-level backtest runs all streams simultaneously. Only after model-level testing passes does the model become deployment-ready.

Locking streams early is fine and expected — it's how we build up the model piece by piece. But no stream's allocation is truly decided until model assembly, when we can see how the streams interact and how much each one fires.

## Tech Stack

- Python, PostgreSQL, Kraken Pro API
- Jupyter Notebooks for backtesting dashboards
- Streamlit (later, for live monitoring)

## Key Constraints

- No leverage, ever
- BTC only (Model 1)
- Limit orders only (0.25% maker fee, 0.50% round trip)
- No LLM in the live execution path — deterministic rules only
- All gains measured as realized cash, not unrealized BTC value
- No real money until backtesting earns it

## Database Tables

Shared (public schema):
- `market_data` — 15m BTC/USD OHLCV candles, Jan 2017 → present
- `sentiment_data` — daily Fear & Greed Index, Feb 2018 → present

Backtest schema (stream tuning + model validation):
- `backtest.stream_tests` — individual stream tuning runs (summary metrics, one row per saved test)
- `backtest.model_tests` — full model backtests (historical and paper, gates live deployment)
- `backtest.models` — model version registry
- `backtest.streams` — stream configs within a model
- `backtest.lots` — per-trade capital state machine for model-level tests

Live schema (real money — not yet built):
- `live.models`, `live.streams`, `live.lots` — mirrors backtest structure, adds Kraken order IDs

Reporting schema (views only):
- `reporting.all_lots` — unions backtest + live lots for cross-environment queries

Full schema: `src/data/schema.sql` and `docs/architecture/database-schema.md`

## Benchmarks

Compare every model against:
1. S&P 500 actual return for the same period
2. BTC buy-and-hold for the same period
3. All prior model versions (head-to-head)
4. Cash (doing nothing)

Track: return, max drawdown, win rate, avg winner/loser, cash efficiency ratio.

## Working Style

- Strong drafts, minimal back-and-forth
- No over-explanation or process narration
- Search and verify before stating claims
- This is a solo side project — keep it lean
