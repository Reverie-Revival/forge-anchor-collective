-- Migration v6 — real per-trade fee columns on live schema
--
-- Purely additive, nullable columns. Unlike v5 (backtest-only), this touches
-- live.* -- real trading data -- so it must be applied to BOTH local Postgres
-- (used by tests/live/) AND Supabase (production).
--
-- NULL entry_fee_usd / fee_usd on existing rows = legacy, opened before this
-- fix -- the code falls back to the MAKER_FEE constant for that one side only
-- when it sees NULL. Same "NULL means pre-tracking" convention as v5. No
-- backfill -- not cleanly reconstructable for rows already closed under the
-- old estimate-only P&L formula.
--
-- fee_is_estimated flags a CLOSED lot/position whose exit numbers came from
-- the fallback estimate (Kraken's post-placement status poll didn't confirm
-- a fill in time) rather than a real confirmed fill -- surfaces in the
-- dashboard as "needs a manual reconciliation glance", not a silent gap.
--
-- Run against local Postgres:
--   psql "$DATABASE_URL" -f src/data/migration_v6_real_fees.sql
-- Run against Supabase:
--   psql "$SUPABASE_DATABASE_URL" -f src/data/migration_v6_real_fees.sql

ALTER TABLE live.lots
    ADD COLUMN IF NOT EXISTS entry_fee_usd    NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS exit_fee_usd     NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS fee_is_estimated BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE live.blended_fills
    ADD COLUMN IF NOT EXISTS fee_usd NUMERIC(10,4);

ALTER TABLE live.blended_positions
    ADD COLUMN IF NOT EXISTS exit_fee_usd     NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS fee_is_estimated BOOLEAN NOT NULL DEFAULT FALSE;
