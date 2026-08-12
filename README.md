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

Model 1 and Model 2 are both live, trading real money in parallel with independent $100 capital each.

**Model 1** — Momentum Rider v2, Dip Hunter v2, Breakout Scout v2 ($33.33/lot each)

**Model 2** — Volume Raider v1, Dip Hunter v3, Breakout Scout v3, Momentum Rider v4 ($25/lot each)

## Key Constraints

- No leverage, ever
- BTC/USD only (Models 1 and 2)
- Limit orders on entry, market orders on exit. Fees are tiered by 30-day Kraken volume, not fixed — currently 0.40% maker / 0.80% taker at this project's volume tier (re-checked live via a fee-drift safeguard, not assumed)
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

## Running the App

```bash
.venv/bin/streamlit run src/app/stream_tester.py --browser.gatherUsageStats false
```

Stream Tester is the entry point; Model Tester, Live Monitor, and Model Dashboard are additional pages under `src/app/pages/` (Streamlit's standard multipage convention — they show up in the sidebar automatically).

## Backfill Sentiment Data

```bash
python -m src.data.sentiment
```

## Project Structure

```
src/
  backtester/    ← backtesting engine + live-replay harness (signals, indicators, engine)
  app/           ← Streamlit apps: Stream Tester, Model Tester, Live Monitor, Model Dashboard
  data/          ← market data downloader, sentiment pipeline, schema, migrations
  live/          ← live execution: executor, order manager, position monitor, Kraken client, alerting
  fees.py        ← single source of truth for MAKER_FEE/TAKER_FEE (shared by backtester + live)
docs/
  decisions/     ← ADRs: vendor choices, architecture decisions
  architecture/  ← system design, data flows, stream attribute system
  specs/         ← strategy stream specs and model definitions
  results/       ← backtest run summaries (populated as models complete)
tools/
  live_replay/   ← replay real strategy code against historical data (Model 1/2/3 gauntlets)
tests/
```

See `docs/` for full architecture documentation.
