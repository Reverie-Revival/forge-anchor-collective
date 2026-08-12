-- Migration v9 — pooled solvency reserve for single/staggered/cascade models
-- (docs/decisions/008)
--
-- New table, live.capital_reserve. Touches live.* (real trading data), so
-- must be applied to BOTH local Postgres (used by tests/live/) AND Supabase
-- (production).
--
-- Context: order_manager.place_entry always sizes every entry off a
-- stream's configured lot_size_usd, with no check at all against real
-- account solvency -- a losing streak can leave less real cash behind a
-- stream than its configured lot size, even though trade *size* itself
-- never shrinks under compound=False (see 2026-08-07 HANDOFF). This table
-- is opt-in: a model with no row here trades exactly as before. Provisioning
-- a row (baseline_total/pool_balance/hard_floor) is a deliberate deployment
-- step, done separately -- this migration only creates the table.
--
-- Deliberately NOT the same table as live.blended_capital -- that one
-- compounds (Model 3/4's own design), this one never grows a stream's
-- trade size above its configured lot_size_usd, it only ever shrinks it
-- (and eventually halts it) once real losses have depleted the pool.
--
-- Run against local Postgres:
--   psql "$DATABASE_URL" -f src/data/migration_v9_capital_reserve.sql
-- Run against Supabase:
--   psql "$SUPABASE_DATABASE_URL" -f src/data/migration_v9_capital_reserve.sql

CREATE TABLE IF NOT EXISTS live.capital_reserve (
    model_id        INTEGER PRIMARY KEY REFERENCES live.models(model_id),
    baseline_total  NUMERIC(12,2) NOT NULL,
    pool_balance    NUMERIC(12,2) NOT NULL,
    hard_floor      NUMERIC(12,2) NOT NULL,
    halted_at       TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
