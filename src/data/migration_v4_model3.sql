-- Migration v4 — Model 3 (Grid Stacker Blended) live infrastructure
--
-- Purely additive: new tables + nullable columns only. Nothing here alters
-- the shape or semantics of any column Model 1's live code already reads or
-- writes. live.lots, live.streams, live.models are untouched.
--
-- Run against both local Postgres (dev/testing) and Supabase (real Model 3 data):
--   psql "$DATABASE_URL" -f src/data/migration_v4_model3.sql
--   psql "$SUPABASE_DATABASE_URL" -f src/data/migration_v4_model3.sql

-- ============================================================
-- Blended position tracking (Model 3 only — one row per stack, not per fill)
-- ============================================================

CREATE TABLE IF NOT EXISTS live.blended_positions (
    position_id             BIGSERIAL PRIMARY KEY,
    model_id                INTEGER      NOT NULL REFERENCES live.models(model_id),
    stream_id               INTEGER      NOT NULL REFERENCES live.streams(stream_id),
    -- PENDING_ENTRY: slot-1 limit order out, no fill yet
    -- OPEN: at least one fill; may also have a pending cascade add in flight
    -- CLOSED: fully exited, P&L realized
    status                   VARCHAR(20)  NOT NULL DEFAULT 'PENDING_ENTRY'
                                 CHECK (status IN ('PENDING_ENTRY', 'OPEN', 'CLOSED')),
    original_entry_price    NUMERIC(12,2),          -- slot 1's fill price; cascade triggers measure off this
    avg_cost_basis           NUMERIC(20,8),          -- total_deployed / total_qty, recomputed on every fill
    total_qty                NUMERIC(20,8) NOT NULL DEFAULT 0,
    total_deployed            NUMERIC(12,2) NOT NULL DEFAULT 0,
    highest_close             NUMERIC(12,2),          -- for the trailing stop, tracked off candle closes
    capitulation_armed        BOOLEAN      NOT NULL DEFAULT FALSE,  -- true once all slots are filled (out of ammo)
    pending_entry_order_id    VARCHAR(50),
    pending_entry_expiry_at   TIMESTAMPTZ,
    pending_add_order_id      VARCHAR(50),
    pending_add_index          SMALLINT,              -- fill_number this pending add would become if filled
    pending_add_expiry_at      TIMESTAMPTZ,
    exit_price                  NUMERIC(12,2),
    exit_order_id                VARCHAR(50),
    closing_capital               NUMERIC(12,2),
    realized_pnl                   NUMERIC(12,2),
    exit_reason                     VARCHAR(30),        -- 'trailing_stop' | 'capitulation_stop'
    opened_at                        TIMESTAMPTZ,
    closed_at                         TIMESTAMPTZ,
    created_at                         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blended_positions_model  ON live.blended_positions (model_id);
CREATE INDEX IF NOT EXISTS idx_blended_positions_status ON live.blended_positions (status);

CREATE TABLE IF NOT EXISTS live.blended_fills (
    fill_id      BIGSERIAL PRIMARY KEY,
    position_id  BIGINT       NOT NULL REFERENCES live.blended_positions(position_id),
    fill_number  SMALLINT     NOT NULL,   -- 0 = slot 1, 1..N = cascade adds
    price        NUMERIC(12,2) NOT NULL,
    capital      NUMERIC(12,2) NOT NULL,
    qty          NUMERIC(20,8) NOT NULL,
    order_id     VARCHAR(50),
    filled_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blended_fills_position ON live.blended_fills (position_id);

-- ============================================================
-- Capital ledger — Model 3's OWN tracked capital (compounds with realized
-- P&L). Position sizing reads this, never Kraken's actual account balance,
-- so Model 3 can never size a trade off money that belongs to Model 1.
-- ============================================================

CREATE TABLE IF NOT EXISTS live.blended_capital (
    model_id           INTEGER PRIMARY KEY REFERENCES live.models(model_id),
    available_capital  NUMERIC(12,2) NOT NULL,
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Additive tagging on shared audit tables — nullable, so Model 1's existing
-- INSERT statements (which don't reference model_id) are unaffected.
-- ============================================================

ALTER TABLE live.executor_runs  ADD COLUMN IF NOT EXISTS model_id INTEGER;
ALTER TABLE live.executor_state ADD COLUMN IF NOT EXISTS model_id INTEGER;

-- Backfill the existing singleton row as belonging to Model 1, and make it
-- the unique key going forward so a new row can be added per model instead
-- of every model fighting over id=1.
UPDATE live.executor_state SET model_id = 1 WHERE id = 1 AND model_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_executor_state_model ON live.executor_state (model_id);

-- Freezes the compounding capital base at the moment a position opens, so
-- slot_capitals (position_capital_base * weight / total_weight) stay fixed
-- for that position's lifetime even as live.blended_capital keeps growing
-- for the NEXT position — same behavior as the backtester's frozen split.
ALTER TABLE live.blended_positions ADD COLUMN IF NOT EXISTS position_capital_base NUMERIC(12,2);
