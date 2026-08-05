-- Migration v7 — live columns for Model 4's blended-position mechanics
--
-- Purely additive, nullable/defaulted columns on live.blended_positions.
-- Touches live.* (real trading data), so must be applied to BOTH local
-- Postgres (used by tests/live/) AND Supabase (production).
--
-- frozen_slot_capitals: the per-slot dollar split computed ONCE at slot-1
-- entry (after applying sentiment tilt, if configured) and never
-- recomputed -- cascade adds and fill logic read this instead of calling
-- slot_capitals_for() fresh each time, because a fresh call would
-- re-evaluate the tilt against a LATER (wrong) Fear & Greed reading.
-- NULL on existing (pre-migration) rows -- those fall back to recomputing
-- from the stream's plain weights, which is correct for any position that
-- never used sentiment_tilt in the first place (Model 3's v8 config never
-- has this key set).
--
-- promotions_used: slot_promotion_days's "impatience" counter, mirrors
-- engine.py's _run_blended_slots in-memory state -- must persist across
-- executor ticks (and restarts) since a position can live for weeks.
--
-- marked_count / marked_capitals: capitulation ladder's per-slot markdown
-- state (oldest-first), same reasoning -- once a slot is marked down it
-- must STAY marked even if price later recovers above that rung, so this
-- can't be recomputed fresh from the current price each tick.
--
-- Run against local Postgres:
--   psql "$DATABASE_URL" -f src/data/migration_v7_model4_mechanics.sql
-- Run against Supabase:
--   psql "$SUPABASE_DATABASE_URL" -f src/data/migration_v7_model4_mechanics.sql

ALTER TABLE live.blended_positions
    ADD COLUMN IF NOT EXISTS frozen_slot_capitals JSONB,
    ADD COLUMN IF NOT EXISTS promotions_used       SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS marked_count           SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS marked_capitals         JSONB;
