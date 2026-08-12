-- Migration v11 — backtest.bucket_tests: persisted results for
-- run_pooled_model_backtest() (docs/decisions/008's pooled-reserve + BTC
-- skim-bucket design), so Model Tester's BTC Bucket section doesn't need
-- a re-run every time the page loads. Backtest schema only (local Postgres)
-- -- mirrors backtest.model_tests' dedup pattern (model_id, preset_id),
-- not the live-money live.btc_bucket table from migration v10.
--
-- Run against local Postgres only:
--   psql "$DATABASE_URL" -f src/data/migration_v11_bucket_tests.sql

CREATE TABLE IF NOT EXISTS backtest.bucket_tests (
    bucket_test_id       SERIAL PRIMARY KEY,
    model_id              INTEGER NOT NULL REFERENCES backtest.models(model_id),
    preset_id             INTEGER REFERENCES timeframe_presets(preset_id),
    custom_start          DATE,
    custom_end            DATE,
    simulation_start      TIMESTAMPTZ,
    simulation_end        TIMESTAMPTZ,
    baseline_total        NUMERIC(12,2),
    final_pool_balance    NUMERIC(12,2),
    total_skimmed         NUMERIC(12,2),
    bucket_cash           NUMERIC(12,2),
    tracked_qty           NUMERIC(20,8),
    tracked_cost_basis    NUMERIC(12,2),
    house_money_qty       NUMERIC(20,8),
    final_price            NUMERIC(12,2),
    final_btc_value        NUMERIC(12,2),
    total_holdings_value   NUMERIC(12,2),
    combined_ending         NUMERIC(12,2),
    skipped_entries          INTEGER,
    hard_floor                NUMERIC(12,2),
    halted_at                  TIMESTAMPTZ,
    bucket_events               JSONB,
    notes                        TEXT,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, preset_id)
);
