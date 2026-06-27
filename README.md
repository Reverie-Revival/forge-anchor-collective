# Forge Anchor Collective

An autonomous BTC trading system that converts market volatility into realized cash returns.

BTC is the vehicle. Cash growth is the product.

## Objective

Can an autonomous system produce annualized returns that beat the S&P 500 (~10% average) while requiring zero human intervention after deployment?

## Architecture

- **5 strategy streams × 2 position slots** = 10 independent $10 positions ($100 total capital)
- **Exit method:** trailing stops (percentages, not fixed dollar amounts — works at any BTC price)
- **AI role:** strategist and designer only. Deterministic code executes trades.

| Stream | Strategy | Personality |
|---|---|---|
| 1 | Trend Following | Ride long moves, wide trailing stops |
| 2 | Dip Buying | Buy panic/capitulation events |
| 3 | Breakout Trading | Enter on breakout confirmations |
| 4 | Sentiment Trading | React to news/social signals |
| 5 | Opportunistic Reserve | Only enters extreme conditions |

## Benchmarks

Every backtest run is compared against:
1. S&P 500 actual return for the same period
2. BTC buy-and-hold for the same period
3. Cash (doing nothing)

## Phases

| Phase | Status | Description |
|---|---|---|
| 1 — Backtest | **Current** | Jan 2025 → present, 9mo train / 3mo validation |
| 2 — Paper Trade | Pending | 30-60 days, no real money |
| 3 — Live Trade | Pending | $100 USDC on Kraken, 6 month minimum |
| 4 — Expand | Pending | Second asset only if Phase 3 succeeds |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in values when needed
```

## Project Structure

```
src/
  data/          ← historical data downloading and storage
  backtester/    ← backtesting engine (streams/slots architecture)
  strategies/    ← individual strategy stream implementations
  analytics/     ← performance reporting vs benchmarks
notebooks/       ← backtest dashboards and exploration
docs/
  decisions/     ← architecture and vendor decision records (ADRs)
  architecture/  ← system design and data flows
  specs/         ← strategy stream specs
  results/       ← backtest run summaries
tests/
```

## Key Constraints

- No leverage, ever
- No LLM in the live execution path
- Limit orders only (0.25% maker fee, 0.50% round trip)
- No real money until backtesting proves the concept
- Only realized cash gains count — not unrealized BTC value

See `docs/decisions/` for the reasoning behind these constraints.
