-- Migration v8 — real limit-sell exits + persisted trail-arm state
--
-- Purely additive, nullable/defaulted columns on live.blended_positions.
-- Touches live.* (real trading data), so must be applied to BOTH local
-- Postgres (used by tests/live/) AND Supabase (production).
--
-- Context: place_exit() used to place an unconditional MARKET sell the
-- instant the trailing-stop/breakeven floor was computed, regardless of
-- whether the market had actually reached that price -- during a crash this
-- could fill far below the intended (supposed to be loss-proof) floor. Fixed
-- by placing a real LIMIT sell at the floor instead, resting on Kraken's own
-- order book until the market genuinely reaches it. Capitulation is
-- unaffected -- it stays an immediate market sell (a deliberate, guaranteed
-- forced cut), only the armed/trailing-stop path changes.
--
-- trail_armed / trail_armed_at: whether this position has ever crossed
-- trail_arm_gain_pct above its (possibly ladder-marked-down) average cost.
-- Previously recomputed fresh every tick and never persisted -- needed now
-- because once armed, capitulation must be permanently disabled for this
-- position (arming proves a real profit path exists; a position that's
-- proven that should never be forced into the deliberate-loss backstop that
-- protects positions that never did). Distinct from the existing
-- capitulation_armed column, which means "all slots filled" (out of ammo),
-- not "profitable" -- do not conflate the two.
--
-- pending_exit_order_id / pending_exit_price / pending_exit_placed_at: the
-- resting limit-sell order tracking, mirroring pending_add_order_id's
-- existing pattern (a non-null column as the sub-state flag, status stays
-- 'OPEN' throughout -- no CHECK constraint change needed). Re-priced (cancel
-- + replace) whenever the computed floor moves; polled for a real fill by
-- the new check_pending_exit(), same shape as check_pending_entry/_add.
--
-- Run against local Postgres:
--   psql "$DATABASE_URL" -f src/data/migration_v8_trail_armed_pending_exit.sql
-- Run against Supabase:
--   psql "$SUPABASE_DATABASE_URL" -f src/data/migration_v8_trail_armed_pending_exit.sql

ALTER TABLE live.blended_positions
    ADD COLUMN IF NOT EXISTS trail_armed             BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS trail_armed_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pending_exit_order_id    VARCHAR(50),
    ADD COLUMN IF NOT EXISTS pending_exit_price       NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS pending_exit_placed_at   TIMESTAMPTZ;
