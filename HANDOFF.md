# Handoff — 2026-07-05 (end of session)

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, Breakout Scout v2 has an open position.

**Architecture Redesign v3 is in progress on `feature/architecture-redesign-v3`.**
Branch is clean and tested locally — ready for browser review before merge.

## Done This Session

### Live Monitor — Progress Bars
Added visual progress bars to Stream Status section (`src/app/pages/live_monitor.py`).
Each condition now shows how close it is to firing — color coded green/yellow/red.
Committed to `main`.

### Architecture Redesign v3 — `feature/architecture-redesign-v3`

#### Schema
- Snapshotted full `backtest` schema → `backtest_bak` (permanent)
- Created `backtest.stream_configs` — versioned params per stream (v1, v2, v1r1, etc.)
- Created `backtest.model_streams` — join table (model_id, stream_config_id, lot_size_usd)
- Stripped `backtest.streams` to identity only (stream_name, strategy_type, description)
- Added `stream_config_id` FK to `stream_tests` and `lots`
- Added `status` + `deployed_at` to `backtest.models`
- Migration run on local postgres — 3 streams, 8 configs, 3 model_stream rows, 39 tests all linked
- Migration file: `src/data/migration_v3.sql` (run this on Supabase before Model 2 dev starts)

#### Engine
- Added `slot_mode='staggered'` to `src/backtester/engine.py`
- `_run_staggered_slots()`: round-robin dispatch to longest-free slot, `slot_entry_gap_candles` cooldown, `slot_capital_weight` for asymmetric sizing
- Smoke tested: DH v2, 2 slots, [70,30] weight → 32 trades, 0 same-candle entries, Slot 1=$14 Slot 2=$6 ✓

#### App
- `src/app/db.py`: new `load_streams()`, `load_stream_configs()`, updated `load_stream_history()` to join via `stream_config_id`, new `save_stream_test()` with upsert on (config, preset)
- `src/app/stream_tester.py`: full rewrite — stream selector shows names only ("Dip Hunter" not "Dip Hunter v2"), second selector for config version, "Run All Presets" button auto-runs + saves all presets in-app, per-preset badges in sidebar, Slot Position as 5th Parameter Reference category

## Next Session

1. **Open the stream tester in browser** — verify new selector flow, "Run All Presets" button, tab results display
2. **Run All Presets for each config** — DH v1/v2, MR v1/v2, BS v1/v2 — populate the DB for all configs
3. **Model tester light update** (optional) — add model composition view from `model_streams`
4. **Merge `feature/architecture-redesign-v3` → main** once browser-verified
5. **Start Model 2 stream design** — leverage staggered slots, focus on regime gaps Model 1 misses

## Branch State

- `main` — stable, live monitor progress bars, executor running
- `live-model-1` — production, runs executor via GitHub Actions — DO NOT TOUCH
- `feature/architecture-redesign-v3` — v3 schema + engine + app rewrites (current work)

## Pending: Supabase Migration

`src/data/migration_v3.sql` has NOT been run on Supabase yet. Only needed when:
- Model 2 development starts and needs the stream_configs / model_streams tables
- No urgency — live executor uses `live.*` schema only, not `backtest.*`

---

## Reference: Architecture

### Branch Strategy (hard rule as of 2026-07-04)
- `main` — all development, workflow files, feature branches merge here
- `live-model-1` — production. GitHub Actions checks this out. **Critical fixes only. No feature work.**
- `feature/*` — new work, always branched from main, merged back to main
- Bug fixes to live: commit to `live-model-1` directly, cherry-pick to `main`

### GitHub Actions Workflows (all on main, workflow_dispatch only, triggered by cron-job.org)
| Workflow | Trigger | What It Does |
|---|---|---|
| `executor.yml` | Every 30 min | Runs `src.live.executor` tick |
| `market_data.yml` | Every 15 min | Fetches candles + updates sentiment (incremental) |

### Streams (Model 1)
- **Momentum Rider v2** (stream_id=1) — 4h \| EMA 30/120 \| 7% trail \| $33.33
- **Dip Hunter v2** (stream_id=2) — 1h \| fear_dip \| RSI≥35 \| 10% trail \| $33.33
- **Breakout Scout v2** (stream_id=3) — 1h \| range_breakout \| SMA200 \| F&G≥55 \| 10% trail \| $33.33

### v3 Schema (local postgres only as of 2026-07-05)
| Table | What it holds |
|---|---|
| `backtest.streams` | Identity only — name, strategy_type |
| `backtest.stream_configs` | Versioned params (v1/v2/etc.) + slot config |
| `backtest.model_streams` | Model composition: which config at what lot_size |
| `backtest.stream_tests` | Test results, dedup on (stream_config_id, preset_id) |
| `backtest_bak.*` | Pre-v3 snapshot, permanent |

### Known Bugs Fixed (lifetime)
| File | Bug | Fix |
|---|---|---|
| `signal_engine.py` | tz-naive/aware mismatch | `.replace(tzinfo=None)` |
| `signal_engine.py` | Sentiment key lookup broken | Match `df.index.date` + `.map(fng_map)` |
| `market_data_updater.py` | Column named `ts` not `timestamp` | Renamed |
| `market_data_updater.py` | Kraken returns `XXBTZUSD` not `XBTUSD` | Fallback key lookup |
| `market_data_updater.py` | Fixed 2h lookback; gaps never self-healed | Fetch from latest DB timestamp |
| `executor.py` | tz-naive/aware in `_latest_candle_for_stream` | `.replace(tzinfo=None)` |
| `kraken_client.py` | `QueryOrders` returns `{}` for taker fills | Fall back to `TradesHistory` |
