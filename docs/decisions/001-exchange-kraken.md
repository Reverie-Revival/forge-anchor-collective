# ADR 001 — Exchange: Kraken Pro

**Date:** 2026-06-26
**Status:** Accepted

## Decision
Use Kraken Pro as the target exchange for live trading and as the source for historical OHLCV data.

## Reasoning
- Maker fee of ~0.25% per side (0.50% round trip) — competitive and predictable
- Public OHLCV API available with no authentication required — simplifies backtesting data pipeline
- Reputable, regulated exchange with a long track record
- Supports limit orders natively — required by our fee strategy
- API supports trading-enabled / withdrawal-disabled key scoping — reduces blast radius if keys are compromised

## Consequences
- All backtesting data comes from Kraken's public endpoints, keeping simulation and live execution on the same source
- Fee model is baked into every backtest from day one (0.50% round trip per trade)
- Every trade must overcome 0.50% before profit begins — this favors fewer, larger moves over high-frequency trading
