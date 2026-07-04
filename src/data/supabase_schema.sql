-- Forge Anchor Collective — Supabase Schema
-- Apply this in Supabase SQL Editor (not the full schema.sql — backtest tables stay local)
-- Safe to re-run — all statements use IF NOT EXISTS / OR REPLACE

CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS reporting;

-- ============================================================
-- SHARED — market_data
-- 15-minute BTC/USD OHLCV candles (last 60 days kept here)
-- ============================================================
CREATE TABLE IF NOT EXISTS market_data (
    candle_id   BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL UNIQUE,
    open        NUMERIC(12,2) NOT NULL,
    high        NUMERIC(12,2) NOT NULL,
    low         NUMERIC(12,2) NOT NULL,
    close       NUMERIC(12,2) NOT NULL,
    volume      NUMERIC(20,8) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_data_timestamp ON market_data (timestamp);

-- ============================================================
-- SHARED — sentiment_data
-- Daily Fear & Greed Index
-- ============================================================
CREATE TABLE IF NOT EXISTS sentiment_data (
    date        DATE PRIMARY KEY,
    fng_value   SMALLINT NOT NULL,
    fng_label   VARCHAR(30) NOT NULL
);

-- ============================================================
-- LIVE SCHEMA
-- ============================================================
CREATE TABLE IF NOT EXISTS live.models (
    model_id                 SERIAL PRIMARY KEY,
    model_version            INTEGER      NOT NULL,
    description              TEXT,
    deployed_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    based_on_model_test_id   INTEGER      NOT NULL,
    status                   VARCHAR(20)  NOT NULL DEFAULT 'active'
                                 CHECK (status IN ('active', 'completed'))
);

CREATE TABLE IF NOT EXISTS live.streams (
    stream_id       SERIAL PRIMARY KEY,
    model_id        INTEGER      NOT NULL REFERENCES live.models(model_id),
    stream_name     VARCHAR(100) NOT NULL,
    stream_version  VARCHAR(10)  NOT NULL,
    strategy_type   VARCHAR(50)  NOT NULL,
    parameters      JSONB        NOT NULL,
    slot_count      SMALLINT     NOT NULL DEFAULT 1 CHECK (slot_count >= 1),
    slot_mode       VARCHAR(30)  NOT NULL DEFAULT 'single',
    lot_size_usd    NUMERIC(10,2) NOT NULL DEFAULT 10.00 CHECK (lot_size_usd >= 10.00)
);

CREATE TABLE IF NOT EXISTS live.lots (
    lot_id           BIGSERIAL PRIMARY KEY,
    model_id         INTEGER      NOT NULL REFERENCES live.models(model_id),
    stream_id        INTEGER      NOT NULL REFERENCES live.streams(stream_id),
    slot_number      SMALLINT     NOT NULL CHECK (slot_number >= 1),
    lot_sequence     INTEGER      NOT NULL,
    status           VARCHAR(10)  NOT NULL DEFAULT 'CASH'
                         CHECK (status IN ('CASH', 'PENDING', 'OPEN', 'CLOSED')),
    opening_capital  NUMERIC(12,2) NOT NULL,
    btc_quantity     NUMERIC(20,8),
    entry_price      NUMERIC(12,2),
    entry_order_id   VARCHAR(50),
    entry_expiry_at  TIMESTAMPTZ,
    high_water_mark  NUMERIC(12,2),
    exit_price       NUMERIC(12,2),
    exit_order_id    VARCHAR(50),
    closing_capital  NUMERIC(12,2),
    realized_pnl     NUMERIC(12,2),
    entry_reason     TEXT,
    exit_reason      TEXT,
    opened_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_live_lots_model  ON live.lots (model_id);
CREATE INDEX IF NOT EXISTS idx_live_lots_stream ON live.lots (stream_id);
CREATE INDEX IF NOT EXISTS idx_live_lots_status ON live.lots (status);

CREATE TABLE IF NOT EXISTS live.executor_state (
    id          INTEGER      PRIMARY KEY DEFAULT 1,
    last_run_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
INSERT INTO live.executor_state (id, last_run_at)
VALUES (1, NOW())
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- REPORTING — view only
-- ============================================================
CREATE OR REPLACE VIEW reporting.all_lots AS
SELECT
    'live'           AS source,
    'live'           AS run_type,
    NULL::integer    AS model_test_id,
    ll.model_id,
    ll.stream_id,
    ll.slot_number,
    ll.lot_sequence,
    ll.status,
    ll.opening_capital,
    ll.closing_capital,
    ll.realized_pnl,
    ll.entry_price,
    ll.exit_price,
    ll.opened_at,
    ll.closed_at,
    ll.entry_reason,
    ll.exit_reason
FROM live.lots ll;
