-- Forge Anchor Collective — Database Schema
-- PostgreSQL 16
-- Run once to initialize: psql -d forge_anchor -f src/data/schema.sql

-- ============================================================
-- SCHEMAS
-- ============================================================
CREATE SCHEMA IF NOT EXISTS backtest;
CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS reporting;

-- ============================================================
-- SHARED — market_data
-- 15-minute BTC/USD OHLCV candles from Jan 1 2017 to present
-- Single interval, single ticker, feeds everything
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
-- BACKTEST SCHEMA
-- Covers both historical simulation runs and paper tests
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest.models (
    model_id        SERIAL PRIMARY KEY,
    model_version   INTEGER NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest.streams (
    stream_id       SERIAL PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES backtest.models(model_id),
    stream_name     VARCHAR(100) NOT NULL,
    stream_version  VARCHAR(10) NOT NULL,
    strategy_type   VARCHAR(50) NOT NULL,
    parameters      JSONB NOT NULL,
    slot_count      SMALLINT NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS backtest.runs (
    run_id                   SERIAL PRIMARY KEY,
    model_id                 INTEGER NOT NULL REFERENCES backtest.models(model_id),
    run_type                 VARCHAR(20) NOT NULL CHECK (run_type IN ('historical', 'paper')),
    simulation_start         TIMESTAMPTZ NOT NULL,
    simulation_end           TIMESTAMPTZ,
    went_live_at             TIMESTAMPTZ,
    status                   VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed')),
    selected_for_deployment  BOOLEAN NOT NULL DEFAULT FALSE,
    configuration            JSONB NOT NULL,
    notes                    TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest.lots (
    lot_id           BIGSERIAL PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES backtest.runs(run_id),
    model_id         INTEGER NOT NULL REFERENCES backtest.models(model_id),
    stream_id        INTEGER NOT NULL REFERENCES backtest.streams(stream_id),
    slot_number      SMALLINT NOT NULL CHECK (slot_number IN (1, 2)),
    lot_sequence     INTEGER NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'CASH' CHECK (status IN ('CASH', 'OPEN', 'CLOSED')),
    opening_capital  NUMERIC(12,2) NOT NULL,
    btc_quantity     NUMERIC(20,8),
    entry_price      NUMERIC(12,2),
    high_water_mark  NUMERIC(12,2),
    exit_price       NUMERIC(12,2),
    closing_capital  NUMERIC(12,2),
    realized_pnl     NUMERIC(12,2),
    entry_reason     TEXT,
    exit_reason      TEXT,
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_backtest_lots_run ON backtest.lots (run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_lots_stream ON backtest.lots (stream_id);
CREATE INDEX IF NOT EXISTS idx_backtest_lots_status ON backtest.lots (status);

-- ============================================================
-- LIVE SCHEMA
-- Real money. Real Kraken orders. Never touched carelessly.
-- ============================================================

CREATE TABLE IF NOT EXISTS live.models (
    model_id          SERIAL PRIMARY KEY,
    model_version     INTEGER NOT NULL,
    description       TEXT,
    deployed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    based_on_run_id   INTEGER NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed'))
);

CREATE TABLE IF NOT EXISTS live.streams (
    stream_id       SERIAL PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES live.models(model_id),
    stream_name     VARCHAR(100) NOT NULL,
    stream_version  VARCHAR(10) NOT NULL,
    strategy_type   VARCHAR(50) NOT NULL,
    parameters      JSONB NOT NULL,
    slot_count      SMALLINT NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS live.lots (
    lot_id           BIGSERIAL PRIMARY KEY,
    model_id         INTEGER NOT NULL REFERENCES live.models(model_id),
    stream_id        INTEGER NOT NULL REFERENCES live.streams(stream_id),
    slot_number      SMALLINT NOT NULL CHECK (slot_number IN (1, 2)),
    lot_sequence     INTEGER NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'CASH' CHECK (status IN ('CASH', 'OPEN', 'CLOSED')),
    opening_capital  NUMERIC(12,2) NOT NULL,
    btc_quantity     NUMERIC(20,8),
    entry_price      NUMERIC(12,2),
    entry_order_id   VARCHAR(50),
    high_water_mark  NUMERIC(12,2),
    exit_price       NUMERIC(12,2),
    exit_order_id    VARCHAR(50),
    closing_capital  NUMERIC(12,2),
    realized_pnl     NUMERIC(12,2),
    entry_reason     TEXT,
    exit_reason      TEXT,
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_live_lots_model ON live.lots (model_id);
CREATE INDEX IF NOT EXISTS idx_live_lots_stream ON live.lots (stream_id);
CREATE INDEX IF NOT EXISTS idx_live_lots_status ON live.lots (status);

-- ============================================================
-- REPORTING SCHEMA — views only, never raw data
-- ============================================================

CREATE OR REPLACE VIEW reporting.all_lots AS
SELECT
    'backtest'          AS source,
    r.run_type,
    r.run_id,
    bl.model_id,
    bl.stream_id,
    bl.slot_number,
    bl.lot_sequence,
    bl.status,
    bl.opening_capital,
    bl.closing_capital,
    bl.realized_pnl,
    bl.entry_price,
    bl.exit_price,
    bl.opened_at,
    bl.closed_at,
    bl.entry_reason,
    bl.exit_reason
FROM backtest.lots bl
JOIN backtest.runs r ON bl.run_id = r.run_id

UNION ALL

SELECT
    'live'              AS source,
    'live'              AS run_type,
    NULL                AS run_id,
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
