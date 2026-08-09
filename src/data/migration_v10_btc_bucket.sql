-- Migration v10 — profit-skim BTC bucket live state (docs/decisions/008)
--
-- New tables, live.btc_bucket and live.btc_bucket_events. Touches live.*
-- (real trading data), so must be applied to BOTH local Postgres (used by
-- tests/live/) AND Supabase (production).
--
-- Context: docs/decisions/008 -- realized gains beyond a stream's fixed
-- lot_size_usd that push a model's pooled reserve (live.capital_reserve,
-- migration v9) above baseline get partially skimmed into this bucket,
-- which buys real BTC on a real dip (stricter than any stream's own entry
-- signal) and sells only enough to recover its own principal once a real
-- premium is cleared, letting the remainder ride as permanent "house
-- money." Mirrors the backtest's simulate_skim_bucket exactly (see
-- src/backtester/model_engine.py's run_pooled_model_backtest, and
-- docs/decisions/007 section 4 for the original mechanics/tuning).
--
-- One bucket per model (not per stream) -- it's funded by the pooled
-- reserve, which is itself model-wide, not stream-scoped.
--
-- Run against local Postgres:
--   psql "$DATABASE_URL" -f src/data/migration_v10_btc_bucket.sql
-- Run against Supabase:
--   psql "$SUPABASE_DATABASE_URL" -f src/data/migration_v10_btc_bucket.sql

CREATE TABLE IF NOT EXISTS live.btc_bucket (
    model_id            INTEGER PRIMARY KEY REFERENCES live.models(model_id),
    bucket_cash         NUMERIC(12,2)  NOT NULL DEFAULT 0,
    tracked_qty         NUMERIC(20,8)  NOT NULL DEFAULT 0,
    tracked_cost_basis  NUMERIC(12,2)  NOT NULL DEFAULT 0,
    house_money_qty     NUMERIC(20,8)  NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

-- Audit trail -- every skim/buy/recovery event, mirroring live.blended_fills'
-- existing pattern of a real per-event log alongside the summary row.
CREATE TABLE IF NOT EXISTS live.btc_bucket_events (
    event_id       BIGSERIAL PRIMARY KEY,
    model_id       INTEGER      NOT NULL REFERENCES live.models(model_id),
    event_type     VARCHAR(20)  NOT NULL CHECK (event_type IN ('skim', 'buy', 'recover_principal')),
    amount_usd     NUMERIC(12,2),          -- skim: $ added to bucket_cash; buy: $ spent; recovery: $ recovered
    qty_btc        NUMERIC(20,8),          -- buy: qty acquired; recovery: qty sold
    price          NUMERIC(12,2),          -- buy/recovery: BTC price at the time
    order_id       VARCHAR(50),            -- buy/recovery: real Kraken order id
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_btc_bucket_events_model ON live.btc_bucket_events (model_id);
