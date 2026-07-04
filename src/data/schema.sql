-- Forge Anchor Collective — Database Schema v2
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
-- SHARED — timeframe_presets
-- Named, reusable date windows for stream and model tests.
-- NULL end_date means "use latest available candle" (open-ended).
-- Custom windows skip preset_id; tests use custom_start/custom_end directly.
-- ============================================================
CREATE TABLE IF NOT EXISTS timeframe_presets (
    preset_id    SERIAL PRIMARY KEY,
    name         VARCHAR(100) NOT NULL UNIQUE,
    start_date   DATE NOT NULL,
    end_date     DATE,                           -- NULL = open-ended (latest available data)
    description  TEXT,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Standard presets (seeded via seed_presets.sql or app UI)
-- Primary Window  : 2019-01-01 → 2023-12-31  (diverse regimes: bull/bear/COVID/recovery)
-- Full History    : 2018-01-01 → present      (includes the 2018 crash; open-ended)
-- Recent          : 2024-01-01 → present      (post-halving era; open-ended)
-- 2026 YTD        : 2026-01-01 → present      (current year stress test; open-ended)

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

-- Individual stream tuning runs.
-- run_number groups tests with identical parameters (same config, different timeframes).
-- Each row is one run against one timeframe (preset or custom).
-- slot_mode + slot_count at test time are stored here so results are self-describing.
CREATE TABLE IF NOT EXISTS backtest.stream_tests (
    test_id               SERIAL PRIMARY KEY,
    stream_name           VARCHAR(100) NOT NULL,
    stream_version        VARCHAR(20)  NOT NULL DEFAULT 'v1',
    run_number            INTEGER      NOT NULL,

    -- Timeframe: use a preset OR custom dates — not both, not neither
    preset_id             INTEGER      REFERENCES timeframe_presets(preset_id),
    custom_start          TIMESTAMPTZ,
    custom_end            TIMESTAMPTZ,            -- NULL = latest available data

    -- Slot configuration captured at test time
    slot_count            SMALLINT     NOT NULL DEFAULT 1 CHECK (slot_count >= 1),
    slot_mode             VARCHAR(30)  NOT NULL DEFAULT 'single',
    -- slot_mode values: 'single' | 'scale_down' | 'scale_up'
    -- slot-specific trigger params (e.g. slot2_trigger_pct) live in parameters JSONB

    -- Actual simulation boundaries (from engine, after warmup clip)
    simulation_start      TIMESTAMPTZ,
    simulation_end        TIMESTAMPTZ,

    -- Strategy parameters
    parameters            JSONB        NOT NULL,
    initial_capital       NUMERIC(10,2) NOT NULL,

    -- Results
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
    saved_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_stream_test_timeframe CHECK (
        (preset_id IS NOT NULL AND custom_start IS NULL)
        OR
        (preset_id IS NULL AND custom_start IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_stream_tests_name_version
    ON backtest.stream_tests (stream_name, stream_version);
CREATE INDEX IF NOT EXISTS idx_stream_tests_run
    ON backtest.stream_tests (stream_name, stream_version, run_number);

-- Streams locked into a model after validation.
-- locked_test_id points to the stream_tests row that earned the lock.
-- slot_mode here is the FINAL operational slot behavior for this stream in this model
-- (may differ from what was tested during tuning runs).
CREATE TABLE IF NOT EXISTS backtest.streams (
    stream_id       SERIAL PRIMARY KEY,
    model_id        INTEGER      NOT NULL REFERENCES backtest.models(model_id),
    stream_name     VARCHAR(100) NOT NULL,
    stream_version  VARCHAR(10)  NOT NULL,
    strategy_type   VARCHAR(50)  NOT NULL,
    parameters      JSONB        NOT NULL,
    slot_count      SMALLINT     NOT NULL DEFAULT 1 CHECK (slot_count >= 1),
    slot_mode       VARCHAR(30)  NOT NULL DEFAULT 'single',
    lot_size_usd    NUMERIC(10,2) NOT NULL DEFAULT 10.00 CHECK (lot_size_usd >= 10.00),
    locked_test_id  INTEGER      REFERENCES backtest.stream_tests(test_id),
    grade           SMALLINT     CHECK (grade BETWEEN 1 AND 5),
    locked_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    description     TEXT,
    notes           TEXT
);

-- Full model-level backtest runs (historical and paper).
-- run_number groups runs with the same allocation config across different timeframes.
CREATE TABLE IF NOT EXISTS backtest.model_tests (
    model_test_id            SERIAL PRIMARY KEY,
    model_id                 INTEGER      NOT NULL REFERENCES backtest.models(model_id),
    run_type                 VARCHAR(20)  NOT NULL CHECK (run_type IN ('historical', 'paper')),
    run_number               INTEGER      NOT NULL,

    -- Timeframe: preset or custom
    preset_id                INTEGER      REFERENCES timeframe_presets(preset_id),
    custom_start             TIMESTAMPTZ,
    custom_end               TIMESTAMPTZ,          -- NULL = latest available data

    -- Execution metadata
    simulation_start         TIMESTAMPTZ  NOT NULL,
    simulation_end           TIMESTAMPTZ,
    went_live_at             TIMESTAMPTZ,
    status                   VARCHAR(20)  NOT NULL DEFAULT 'completed'
                                 CHECK (status IN ('running', 'completed')),
    selected_for_deployment  BOOLEAN      NOT NULL DEFAULT FALSE,
    configuration            JSONB        NOT NULL,

    -- Results
    total_capital            NUMERIC(10,2),
    ending_balance           NUMERIC(10,4),
    total_trades             INTEGER,
    win_rate                 NUMERIC(5,4),
    total_pnl                NUMERIC(10,4),
    total_return_pct         NUMERIC(8,2),
    annualized_return_pct    NUMERIC(8,2),
    max_drawdown_pct         NUMERIC(8,2),
    notes                    TEXT,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_model_test_timeframe CHECK (
        (preset_id IS NOT NULL AND custom_start IS NULL)
        OR
        (preset_id IS NULL AND custom_start IS NOT NULL)
    )
);

-- Per-trade capital state machine for model-level tests.
-- CASH → OPEN → CLOSED; capital compounds within a slot.
-- Each lot has its own high_water_mark — trailing stops are independent per lot.
CREATE TABLE IF NOT EXISTS backtest.lots (
    lot_id           BIGSERIAL PRIMARY KEY,
    model_test_id    INTEGER      NOT NULL REFERENCES backtest.model_tests(model_test_id),
    model_id         INTEGER      NOT NULL REFERENCES backtest.models(model_id),
    stream_id        INTEGER      NOT NULL REFERENCES backtest.streams(stream_id),
    slot_number      SMALLINT     NOT NULL CHECK (slot_number >= 1),
    lot_sequence     INTEGER      NOT NULL,
    status           VARCHAR(10)  NOT NULL DEFAULT 'CASH'
                         CHECK (status IN ('CASH', 'OPEN', 'CLOSED')),
    opening_capital  NUMERIC(12,2) NOT NULL,
    btc_quantity     NUMERIC(20,8),
    entry_price      NUMERIC(12,2),
    high_water_mark  NUMERIC(12,2),
    exit_price       NUMERIC(12,2),
    closing_capital  NUMERIC(12,2),
    realized_pnl     NUMERIC(12,2),
    entry_reason     TEXT,
    exit_reason      TEXT,
    opened_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_backtest_lots_model_test ON backtest.lots (model_test_id);
CREATE INDEX IF NOT EXISTS idx_backtest_lots_stream     ON backtest.lots (stream_id);
CREATE INDEX IF NOT EXISTS idx_backtest_lots_status     ON backtest.lots (status);

-- ============================================================
-- LIVE SCHEMA
-- Real money. Real Kraken orders. Never touched carelessly.
-- Not yet built — placeholder tables only.
-- ============================================================

CREATE TABLE IF NOT EXISTS live.models (
    model_id                 SERIAL PRIMARY KEY,
    model_version            INTEGER      NOT NULL,
    description              TEXT,
    deployed_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    based_on_model_test_id   INTEGER      NOT NULL,   -- references backtest.model_tests
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
    -- CASH: slot available; PENDING: limit order placed, awaiting fill;
    -- OPEN: position held; CLOSED: exited, P&L realized
    status           VARCHAR(10)  NOT NULL DEFAULT 'CASH'
                         CHECK (status IN ('CASH', 'PENDING', 'OPEN', 'CLOSED')),
    opening_capital  NUMERIC(12,2) NOT NULL,
    btc_quantity     NUMERIC(20,8),
    entry_price      NUMERIC(12,2),
    entry_order_id   VARCHAR(50),
    entry_expiry_at  TIMESTAMPTZ,              -- cancel limit order if unfilled after this time
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

CREATE TABLE IF NOT EXISTS live.executor_runs (
    run_id          SERIAL       PRIMARY KEY,
    ran_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_tick_at    TIMESTAMPTZ,
    closed_tfs      TEXT[],
    open_lots       INT,
    pending_lots    INT,
    signals_fired   TEXT[],
    entries_placed  INT,
    fills           INT,
    expirations     INT,
    stops_triggered INT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_executor_runs_ran_at ON live.executor_runs (ran_at);

CREATE TABLE IF NOT EXISTS live.market_data_runs (
    run_id          SERIAL       PRIMARY KEY,
    ran_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    candles_fetched INT,
    latest_candle   TIMESTAMPTZ,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_market_data_runs_ran_at ON live.market_data_runs (ran_at);

-- ============================================================
-- REPORTING SCHEMA — views only, never raw data
-- ============================================================

CREATE OR REPLACE VIEW reporting.all_lots AS
SELECT
    'backtest'       AS source,
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
    'live'           AS source,
    'live'           AS run_type,
    NULL             AS model_test_id,
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
