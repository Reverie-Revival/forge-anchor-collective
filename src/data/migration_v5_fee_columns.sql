-- Migration v5 — fee-rate columns on backtest results
--
-- Purely additive: two nullable NUMERIC columns on backtest.stream_tests and
-- backtest.model_tests. Backtest schema only, freely rebuilt -- not touched
-- on Supabase (live schema has no equivalent tables).
--
-- Rows saved before this migration have NULL fee columns -- that's the
-- "legacy" marker the app uses to flag pre-fee-tracking results. Nothing
-- here backfills old rows; see HANDOFF.md 2026-08-03 for why (three
-- different fee regimes existed historically, not cleanly separable by
-- timestamp alone -- not worth reconstructing for exploratory runs nobody
-- will act on again).
--
-- Run:
--   psql "$DATABASE_URL" -f src/data/migration_v5_fee_columns.sql

ALTER TABLE backtest.stream_tests
    ADD COLUMN IF NOT EXISTS fee_maker_pct NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS fee_taker_pct NUMERIC(6,4);

ALTER TABLE backtest.model_tests
    ADD COLUMN IF NOT EXISTS fee_maker_pct NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS fee_taker_pct NUMERIC(6,4);
