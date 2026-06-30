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
)
ON CONFLICT (name) DO NOTHING;
