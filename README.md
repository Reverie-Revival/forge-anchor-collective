# Forge Anchor Collective

An autonomous BTC trading system that converts market volatility into realized cash returns.

BTC is the vehicle. Cash growth is the product. Goal: beat S&P 500 annualized returns (~10%) with zero human intervention.

## How It Works

Each **Model** is a collection of 5 independent strategy streams, each running 2 capital slots of $10 each — 10 positions, $100 total. Models are versioned and deployed in parallel with their own capital. They run indefinitely and are never shut down early.

Every 2-3 months, a new model is designed, backtested, and deployed alongside the existing ones if backtesting earns it. Over time, the tournament generates real performance data to compare strategy lineages head-to-head.

## Model Tournament

| Grade | Label | Criteria |
|---|---|---|
| 5 | Elite | 20%+ annualized, sustained 2+ years |
| 4 | Strong | Consistently beats S&P (10-19%) |
| 3 | Passing | Roughly matches S&P (8-12%) |
| 2 | Weak | Positive but below S&P |
| 1 | Poor | Break-even or loss |

S&P 500 (~10%) is the midpoint. Poor results are data, not failure.

## Current Status

Model 1 is in the build phase. Five candidate streams are designed and under backtesting:

| Stream | Type |
|---|---|
| Momentum Rider v1 | Trend-following (EMA crossover) |
| Dip Hunter v1 | Mean reversion (RSI dip) |
| Breakout Scout v1 | Consolidation breakout |
| Surge Rider v1 | Volume momentum |
| Steady Climber v1 | Trend-filtered pullback |

## Key Constraints

- No leverage, ever
- BTC/USD only (Model 1)
- Limit orders only (0.25% maker fee, 0.50% round trip)
- No LLM in the live execution path — deterministic rules only
- All gains measured as realized cash, not unrealized BTC value
- No real money until backtesting earns it

## Tech Stack

Python · PostgreSQL · Kraken Pro API · Streamlit

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add DATABASE_URL
```

## Running the Stream Tester

```bash
.venv/bin/streamlit run src/app/stream_tester.py --browser.gatherUsageStats false
```

## Backfill Sentiment Data

```bash
python -m src.data.sentiment
```

## Project Structure

```
src/
  backtester/    ← backtesting engine (signals, indicators, engine, runner)
  app/           ← Streamlit stream tester
  data/          ← market data downloader, sentiment pipeline, schema
  strategies/    ← live execution (not yet built)
  analytics/     ← reporting (not yet built)
docs/
  decisions/     ← ADRs: vendor choices, architecture decisions
  architecture/  ← system design, data flows, stream attribute system
  specs/         ← strategy stream specs and model definitions
  results/       ← backtest run summaries (populated as models complete)
tests/
```

See `docs/` for full architecture documentation.
