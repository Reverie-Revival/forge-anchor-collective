-- Forge Anchor Collective — Standard Timeframe Presets
-- Run after schema.sql: psql -d forge_anchor -f src/data/seed_presets.sql
-- Safe to re-run — uses ON CONFLICT DO NOTHING

INSERT INTO timeframe_presets (name, start_date, end_date, description) VALUES
(
    'Primary Window',
    '2019-01-01',
    '2023-12-31',
    'Core validation window: Jan 2019 – Dec 2023. Covers the 2019-2020 bear, COVID crash, 2021 bull, 2022 bear. Fixed endpoints for consistent cross-stream comparison.'
),
(
    'Full History',
    '2018-01-01',
    NULL,
    'All available data from Jan 2018 forward. Open-ended: uses latest available candle. Includes the 2018 crash. Note: 2017 excluded to keep start-date consistent across all streams.'
),
(
    'Recent',
    '2024-01-01',
    NULL,
    'Post-halving era from Jan 2024 forward. Open-ended. Good stress test for current market regime.'
),
(
    '2026 YTD',
    '2026-01-01',
    NULL,
    'Current year only. Open-ended. Use for short-term performance checks during live operation.'
),
(
    'Primary v2',
    '2022-01-01',
    NULL,
    'Open-ended validation window starting 2022 -- added later as the default build/comparison window (see HANDOFF.md). Was previously only in the DB, not this seed file.'
),
(
    'Model 1 Live Sync',
    '2026-07-03',
    NULL,
    'Starts at Model 1''s real go-live moment (2026-07-03) through the latest available candle. Open-ended -- re-run anytime (per-stream in Stream Tester, or combined in Model Tester) to check the backtester still reproduces what the live account actually did. Not a validation window, a sync check.'
),
(
    'Model 3 Live Sync',
    '2026-08-02',
    NULL,
    'Starts at Model 3''s real go-live moment (2026-08-02) through the latest available candle. Open-ended -- re-run anytime to check the backtester still reproduces what the live account actually did (same entries, same timing, same current state). Not a validation window, a sync check.'
)
ON CONFLICT (name) DO NOTHING;
