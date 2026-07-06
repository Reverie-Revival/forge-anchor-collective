-- Forge Anchor — Architecture Redesign v3 Migration
-- Run ONCE on each database (local + Supabase).
-- Safe to inspect/audit before running — all changes are inside a transaction.
-- Run: psql $DATABASE_URL -f src/data/migration_v3.sql

BEGIN;

-- ============================================================
-- STEP 1: Snapshot backtest schema → backtest_bak
-- Preserves all pre-migration data permanently.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS backtest_bak;

CREATE TABLE IF NOT EXISTS backtest_bak.models
    AS SELECT * FROM backtest.models;

CREATE TABLE IF NOT EXISTS backtest_bak.streams
    AS SELECT * FROM backtest.streams;

CREATE TABLE IF NOT EXISTS backtest_bak.stream_tests
    AS SELECT * FROM backtest.stream_tests;

CREATE TABLE IF NOT EXISTS backtest_bak.model_tests
    AS SELECT * FROM backtest.model_tests;

CREATE TABLE IF NOT EXISTS backtest_bak.lots
    AS SELECT * FROM backtest.lots;

-- ============================================================
-- STEP 2: Add new columns to backtest.models
-- ============================================================

ALTER TABLE backtest.models
    ADD COLUMN IF NOT EXISTS status       VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'archived')),
    ADD COLUMN IF NOT EXISTS deployed_at  TIMESTAMPTZ;

-- ============================================================
-- STEP 3: Create backtest.stream_configs
-- One row per unique (stream, version, run_number) param set.
-- Existing data versioned as "v1r1", "v1r2", "v2r4", etc.
-- New configs going forward use clean labels: "v1", "v2", "v3".
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest.stream_configs (
    stream_config_id  SERIAL PRIMARY KEY,
    stream_id         INTEGER      NOT NULL REFERENCES backtest.streams(stream_id),
    version           VARCHAR(20)  NOT NULL,
    parameters        JSONB        NOT NULL,
    slot_count        SMALLINT     NOT NULL DEFAULT 1 CHECK (slot_count >= 1),
    slot_mode         VARCHAR(30)  NOT NULL DEFAULT 'single',
    -- slot_mode values: 'single' | 'staggered' | 'scale_down' | 'scale_up'
    notes             TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (stream_id, version)
);

CREATE INDEX IF NOT EXISTS idx_stream_configs_stream
    ON backtest.stream_configs (stream_id);

-- ============================================================
-- STEP 4: Populate stream_configs from existing stream_tests
-- Each unique (stream_name, stream_version, run_number) → one config row.
-- Parameters taken from the first test_id for that combo (all identical).
-- ============================================================

INSERT INTO backtest.stream_configs (stream_id, version, parameters, slot_count, slot_mode, created_at)
SELECT DISTINCT ON (st.stream_name, st.stream_version, st.run_number)
    s.stream_id,
    -- Version label: if only one run_number exists for this stream+version, use stream_version.
    -- Otherwise append run number: "v1r1", "v1r2", etc.
    CASE
        WHEN counts.run_count = 1 THEN st.stream_version
        ELSE st.stream_version || 'r' || st.run_number
    END AS version,
    st.parameters,
    st.slot_count,
    st.slot_mode,
    MIN(st.saved_at) OVER (PARTITION BY st.stream_name, st.stream_version, st.run_number)
FROM backtest.stream_tests st
JOIN backtest.streams s ON s.stream_name = st.stream_name
JOIN (
    SELECT stream_name, stream_version, COUNT(DISTINCT run_number) AS run_count
    FROM backtest.stream_tests
    GROUP BY stream_name, stream_version
) counts ON counts.stream_name = st.stream_name AND counts.stream_version = st.stream_version
ORDER BY st.stream_name, st.stream_version, st.run_number, st.test_id
ON CONFLICT (stream_id, version) DO NOTHING;

-- ============================================================
-- STEP 5: Create backtest.model_streams (model composition)
-- Ties a model version to the stream configs it uses + allocation.
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest.model_streams (
    id                SERIAL PRIMARY KEY,
    model_id          INTEGER      NOT NULL REFERENCES backtest.models(model_id),
    stream_config_id  INTEGER      NOT NULL REFERENCES backtest.stream_configs(stream_config_id),
    lot_size_usd      NUMERIC(10,2) NOT NULL DEFAULT 10.00 CHECK (lot_size_usd >= 10.00),
    UNIQUE (model_id, stream_config_id)
);

-- Populate model_streams from the existing backtest.streams rows.
-- Each stream in backtest.streams was locked at a specific version;
-- match to the stream_config that corresponds to that (stream, version, run_number).
-- For the existing Model 1 streams (all v2), this maps to the "v2" stream_config.
INSERT INTO backtest.model_streams (model_id, stream_config_id, lot_size_usd)
SELECT
    s.model_id,
    sc.stream_config_id,
    s.lot_size_usd
FROM backtest.streams s
JOIN backtest.stream_configs sc
    ON sc.stream_id = s.stream_id
    AND sc.version = s.stream_version
ON CONFLICT (model_id, stream_config_id) DO NOTHING;

-- ============================================================
-- STEP 6: Add stream_config_id to backtest.stream_tests
-- ============================================================

ALTER TABLE backtest.stream_tests
    ADD COLUMN IF NOT EXISTS stream_config_id INTEGER
        REFERENCES backtest.stream_configs(stream_config_id);

-- Populate: match on (stream_name, stream_version, run_number)
UPDATE backtest.stream_tests st
SET stream_config_id = sc.stream_config_id
FROM backtest.stream_configs sc
JOIN backtest.streams s ON sc.stream_id = s.stream_id
WHERE s.stream_name = st.stream_name
  AND (
      sc.version = st.stream_version
      OR sc.version = st.stream_version || 'r' || st.run_number
  );

-- ============================================================
-- STEP 7: Add stream_config_id to backtest.lots
-- (lots is empty locally; column added for forward-compatibility)
-- ============================================================

ALTER TABLE backtest.lots
    ADD COLUMN IF NOT EXISTS stream_config_id INTEGER
        REFERENCES backtest.stream_configs(stream_config_id);

-- ============================================================
-- STEP 8: Strip backtest.streams to identity-only
-- Remove columns now owned by stream_configs and model_streams.
-- Keep: stream_id, stream_name, strategy_type, description, created_at
-- ============================================================

-- Add created_at if missing (it wasn't in original schema)
ALTER TABLE backtest.streams
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Drop columns that have moved to stream_configs / model_streams
ALTER TABLE backtest.streams
    DROP COLUMN IF EXISTS model_id,
    DROP COLUMN IF EXISTS stream_version,
    DROP COLUMN IF EXISTS parameters,
    DROP COLUMN IF EXISTS slot_count,
    DROP COLUMN IF EXISTS slot_mode,
    DROP COLUMN IF EXISTS lot_size_usd,
    DROP COLUMN IF EXISTS locked_test_id,
    DROP COLUMN IF EXISTS grade,
    DROP COLUMN IF EXISTS locked_at,
    DROP COLUMN IF EXISTS notes;

-- Rename description_new → description if it exists (it was the old text column)
-- The existing description column stays; nothing to rename.

-- ============================================================
-- STEP 9: Drop old stream_tests indexes that referenced stream_name+version
-- (no longer the primary lookup path — stream_config_id is)
-- ============================================================

DROP INDEX IF EXISTS backtest.idx_stream_tests_name_version;
DROP INDEX IF EXISTS backtest.idx_stream_tests_run;

CREATE INDEX IF NOT EXISTS idx_stream_tests_config
    ON backtest.stream_tests (stream_config_id);
CREATE INDEX IF NOT EXISTS idx_stream_tests_config_preset
    ON backtest.stream_tests (stream_config_id, preset_id);

COMMIT;

-- ============================================================
-- VERIFY
-- ============================================================
-- After running, confirm:
--   SELECT * FROM backtest.streams;                      -- 3 identity rows
--   SELECT * FROM backtest.stream_configs;               -- 6+ config rows
--   SELECT * FROM backtest.model_streams;                -- 3 composition rows
--   SELECT COUNT(*) FROM backtest.stream_tests WHERE stream_config_id IS NOT NULL;
--   SELECT * FROM backtest_bak.streams;                  -- original rows preserved
