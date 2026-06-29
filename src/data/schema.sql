-- Forge Anchor Collective — Database Schema
-- PostgreSQL 16
-- Run once to initialize: psql -d forge_anchor -f src/data/schema.sql
-- Safe to re-run — all statements use IF NOT EXISTS / OR REPLACE

-- ============================================================
-- SCHEMAS
-- ============================================================
CREATE SCHEMA IF NOT EXISTS backtest;
CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS reporting;

-- ============================================================
-- SHARED — market_data
-- 15-minute BTC/USD OHLCV candles, Jan 2017 → present
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
-- SHARED — sentiment_data
-- Daily Fear & Greed Index, Feb 2018 → present
-- Backfill: python -m src.data.sentiment
-- ============================================================
CREATE TABLE IF NOT EXISTS sentiment_data (
    date        DATE PRIMARY KEY,
    fng_value   SMALLINT NOT NULL,       -- 0 (Extreme Fear) → 100 (Extreme Greed)
    fng_label   VARCHAR(30) NOT NULL     -- "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
);

-- ============================================================
-- BACKTEST SCHEMA
-- Stream-level tuning and full model-level testing.
-- Freely rebuilt during development. Never real money.
-- ============================================================

-- One row per model version defined for backtesting.
CREATE TABLE IF NOT EXISTS backtest.models (
    model_id        SERIAL PRIMARY KEY,
    model_version   INTEGER NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Streams locked into a model after validation.
-- locked_test_id points to the stream_tests row (Primary window) that earned the lock.
CREATE TABLE IF NOT EXISTS backtest.streams (
    stream_id       SERIAL PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES backtest.models(model_id),
    stream_name     VARCHAR(100) NOT NULL,
    stream_version  VARCHAR(10) NOT NULL,
    strategy_type   VARCHAR(50) NOT NULL,
    parameters      JSONB NOT NULL,
    slot_count      SMALLINT NOT NULL DEFAULT 2,
    locked_test_id  INTEGER REFERENCES backtest.stream_tests(test_id),
    grade           SMALLINT,
    locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description     TEXT,
    notes           TEXT
);

-- Individual stream tuning runs — saved when worth keeping during iteration.
-- run_number groups tests with identical params (same config, different date windows).
-- window_name labels the date range: Primary Window / Full History / Recent / custom.
CREATE TABLE IF NOT EXISTS backtest.stream_tests (
    test_id               SERIAL PRIMARY KEY,
    stream_name           VARCHAR(100) NOT NULL,
    stream_version        VARCHAR(20)  NOT NULL DEFAULT 'v1',
    run_number            INTEGER,
    window_name           VARCHAR(50),
    parameters            JSONB NOT NULL,
    test_start            TIMESTAMPTZ,
    test_end              TIMESTAMPTZ,
    n_slots               SMALLINT     NOT NULL DEFAULT 2,
    initial_capital       NUMERIC(10,2) NOT NULL,
    ending_balance        NUMERIC(10,4),
    total_trades          INTEGER,
    win_rate              NUMERIC(5,4),
    total_pnl             NUMERIC(10,4),
    total_return_pct      NUMERIC(8,2),
    annualized_return_pct NUMERIC(8,2),
    avg_winner_pct        NUMERIC(8,2),
    avg_loser_pct         NUMERIC(8,2),
    profit_factor         NUMERIC(8,2),
    max_drawdown_pct      NUMERIC(8,2),
    avg_hold_candles      NUMERIC(8,1),
    notes                 TEXT,
    saved_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Full model-level backtest runs (historical and paper).
-- Used to validate a complete model before live deployment.
CREATE TABLE IF NOT EXISTS backtest.model_tests (
    model_test_id            SERIAL PRIMARY KEY,
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

-- Per-trade capital state machine for model-level tests.
-- CASH → OPEN → CLOSED; capital compounds within a slot.
CREATE TABLE IF NOT EXISTS backtest.lots (
    lot_id           BIGSERIAL PRIMARY KEY,
    model_test_id    INTEGER NOT NULL REFERENCES backtest.model_tests(model_test_id),
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

CREATE INDEX IF NOT EXISTS idx_backtest_lots_model_test ON backtest.lots (model_test_id);
CREATE INDEX IF NOT EXISTS idx_backtest_lots_stream ON backtest.lots (stream_id);
CREATE INDEX IF NOT EXISTS idx_backtest_lots_status ON backtest.lots (status);

-- ============================================================
-- LIVE SCHEMA
-- Real money. Real Kraken orders. Never touched carelessly.
-- Not yet built — mirrors backtest structure with Kraken order IDs added.
-- ============================================================

CREATE TABLE IF NOT EXISTS live.models (
    model_id            SERIAL PRIMARY KEY,
    model_version       INTEGER NOT NULL,
    description         TEXT,
    deployed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    based_on_model_test_id  INTEGER NOT NULL,   -- references backtest.model_tests
    status              VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed'))
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
-- All analytics queries go through these views.
-- ============================================================

CREATE OR REPLACE VIEW reporting.all_lots AS
SELECT
    'backtest'          AS source,
    mt.run_type,
    mt.model_test_id,
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
JOIN backtest.model_tests mt ON bl.model_test_id = mt.model_test_id

UNION ALL

SELECT
    'live'              AS source,
    'live'              AS run_type,
    NULL                AS model_test_id,
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
