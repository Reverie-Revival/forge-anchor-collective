# ADR 005 — Three-Environment Testing Architecture

**Date:** 2026-06-26
**Status:** Accepted

## Decision
All testing and trading activity lives in one of three environments: historical backtest, paper test, or live. These map to two PostgreSQL schemas: `backtest` (covers both historical and paper) and `live` (real money only).

## The Three Environments

### Historical Backtest
- Runs against stored market_data (past price history)
- Fast — can simulate months in seconds
- Run as many times as needed with different configurations
- Purpose: exploratory, find the best stream combinations

### Paper Test
- Runs against live real-time Kraken data (no real orders placed)
- Runs at real clock speed — cannot be fast-forwarded from the live date
- Multiple paper tests can run simultaneously for the same model
- Has a configurable simulation start date — regardless of when you kick it off, it replays history from that date first, then transitions to real-time
- Purpose: prove a specific configuration against live market conditions before committing real money
- Runs in perpetuity until a deployment decision is made

### Live
- Real money on Kraken — limit orders actually placed
- One deployment per model version
- Runs indefinitely per the model commitment rule
- Precious — never touched carelessly

## Why Two Schemas, Not Three
Historical backtest and paper test share the same lot structure, trade structure, and performance metrics. The only differences are data source (historical vs live feed) and clock speed. A `run_type` column on `backtest.runs` handles this distinction cleanly without schema duplication.

The real boundary that matters is **real money vs not**. That boundary is enforced by the schema split.

## Multiple Concurrent Paper Tests
Each model candidate can have multiple paper tests running simultaneously, each testing a different stream configuration. They are not replaced when a better configuration is found — all run in perpetuity until deployment.

When it is time to deploy, the best-performing paper test is selected. `selected_for_deployment = TRUE` marks the winner. All others continue running as reference data.

## Configurable Simulation Start Date
Every paper test has a `simulation_start` date that can be set to any historical date — typically the date Model N-1 went live, so all paper tests for Model N can be compared on equal footing regardless of when they were started.

**Two phases of a paper test:**
1. Historical replay — fast-forwards from `simulation_start` to today using stored market_data
2. Live real-time — transitions to Kraken live feed and runs forward indefinitely

## Traceability: Paper Test → Live Deployment
`live.models.based_on_run_id` links every live model deployment back to the specific paper test it was selected from. This creates an auditable chain: experimental backtest found the config → paper test proved it → live model deployed from it.

## Consequences
- `backtest.runs.run_type` distinguishes 'historical' from 'paper'
- Paper tests need a `went_live_at` timestamp marking the replay-to-realtime transition
- `selected_for_deployment` boolean on `backtest.runs` marks paper test winners
- `live.models` carries a `based_on_run_id` FK to `backtest.runs`
- See `docs/architecture/database-schema.md` for full table definitions
- See `docs/architecture/validation-workflow.md` for the full model lifecycle
