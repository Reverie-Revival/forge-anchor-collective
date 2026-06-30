-- Forge Anchor Collective — Backtest Schema Reset
-- Drops all backtest data and rebuilds clean.
-- market_data and sentiment_data are NOT touched.
--
-- Usage:
--   psql -d forge_anchor -f src/data/reset_backtest.sql
--   psql -d forge_anchor -f src/data/schema.sql
--   psql -d forge_anchor -f src/data/seed_presets.sql

-- Drop reporting view first (depends on backtest.lots)
DROP VIEW IF EXISTS reporting.all_lots;

-- Drop backtest tables in dependency order
DROP TABLE IF EXISTS backtest.lots       CASCADE;
DROP TABLE IF EXISTS backtest.model_tests CASCADE;
DROP TABLE IF EXISTS backtest.streams    CASCADE;
DROP TABLE IF EXISTS backtest.stream_tests CASCADE;
DROP TABLE IF EXISTS backtest.models     CASCADE;

-- Drop timeframe_presets (v2 addition — may not exist yet)
DROP TABLE IF EXISTS timeframe_presets   CASCADE;

-- live.* is intentionally excluded from this script.
-- Live tables hold real Kraken positions and must never be dropped casually.
-- Live schema changes require a dedicated, explicitly reviewed migration.
