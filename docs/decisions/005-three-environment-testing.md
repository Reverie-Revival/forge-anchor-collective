# ADR 005 — Testing Architecture

**Date:** 2026-06-26
**Status:** Accepted (updated 2026-07-05)

## Decision
Testing lives in two environments: historical backtest and live. There is no mandatory paper trading phase. $100 live capital is low enough that live deployment IS the real-world test. Backtest confidence is the gate, not a calendar waiting period.

## The Two Environments

### Historical Backtest
- Runs against stored `market_data` (past 15m candles from Jan 2017 → present)
- Fast — simulates months in seconds
- Run as many times as needed with different configurations
- Two levels: **stream testing** (tune a single stream's signal in isolation) and **model testing** (all streams run simultaneously, capital allocation set)
- Both stored in the `backtest` schema

### Live
- Real money on Kraken — limit orders actually placed
- One deployed model per model version, runs indefinitely
- Precious — never touched carelessly
- Stored in the `live` schema

## Why No Mandatory Paper Testing
Paper testing at real clock speed adds weeks of calendar time before any real-world validation. Given the $100 capital commitment per model, live deployment *is* the validation. A paper test that runs for a week tells us far less than a $100 live deployment that runs for months.

Paper testing remains available as an optional mode if there is a specific reason to validate a configuration in real-time before committing capital (e.g., testing live data pipeline reliability). It is not required by the model lifecycle.

## Two Levels of Backtest

### Stream Tests
- Stored in `backtest.stream_tests`, keyed on `(stream_config_id, preset_id)`
- Tune signal parameters in isolation — no capital allocation decisions
- Re-running the same config + preset replaces the existing result (upsert, no duplicates)
- Purpose: find the best signal configuration for each stream before model assembly

### Model Tests
- Stored in `backtest.model_tests`, keyed on `model_id` + run metadata
- All streams run simultaneously with final allocation weights set in `backtest.model_streams`
- Must pass model-level validation before deployment is approved
- Purpose: prove the full system configuration as a unit

## Schema Split: Backtest vs Live
The real boundary that matters is **real money vs not**. That boundary is enforced by the schema split (`backtest.*` vs `live.*`). Within backtest, `model_tests.run_type` distinguishes `'historical'` from `'paper'` for any optional paper runs.

## Consequences
- `backtest.stream_tests` deduplicates on `(stream_config_id, preset_id)` — re-running is safe
- `backtest.model_tests.run_type` column handles optional paper runs without schema duplication
- No paper test is required or expected in the standard model lifecycle
- `live.models` does not require a paper test linkage — deployment is decided from backtest results
- See `docs/architecture/database-schema.md` for full table definitions
