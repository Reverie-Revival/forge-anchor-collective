# Handoff — 2026-07-05 (end of session)

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, cron on schedule.

**`feature/architecture-redesign-v3` merged to `main`** — v3 schema, engine, and app are live on main.

## Done This Session

### Architecture Redesign v3 — merged to main

**Schema (local postgres):**
- `backtest.streams` — identity only; `backtest.stream_configs` — versioned params; `backtest.model_streams` — composition join
- `migration_v3.sql` NOT yet run on Supabase — only needed when Model 2 dev starts

**Engine:**
- Staggered slot mode added (`_run_staggered_slots()` in `engine.py`)
- Round-robin dispatch to longest-free slot, `slot_entry_gap_candles`, `slot_capital_weight`

**App:**
- `stream_tester.py`: full rewrite — stream name selector → config version selector → "Run All Presets" auto-save
- `db.py`: new `load_streams()`, `load_stream_configs()`, updated `save_stream_test()` with upsert on (config, preset)
- Pages renamed: `pages/1_model_tester.py`, `pages/2_live_monitor.py` (fixes page order)
- Use `localhost:8502` (app.py instance with `layout="wide"`)

**Docs fully updated:**
- ADRs 001, 004, 005 updated (Coinbase data source, 3-5 streams, removed mandatory paper testing)
- Stream specs: BS v1 deleted; DH v2 and MR v2 created with locked params; v1 specs marked superseded
- `model-1.md`: correct links, deployed status, completed gate checklist
- `validation-workflow.md`: rewritten — reflects two-phase flow (stream tuning → model assembly → deploy)

## Next Session

1. **Run All Presets for each config** in Stream Tester — populate DB for DH v1/v2, MR v1/v2, BS v1/v2
2. **Start Model 2 stream design** — leverage staggered slots, focus on regime gaps Model 1 misses
3. **Run Supabase migration** (`src/data/migration_v3.sql`) when Model 2 dev starts

## Branch State

- `main` — current, v3 architecture merged
- `live-model-1` — production, runs executor via GitHub Actions — DO NOT TOUCH
- No active feature branches

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
- **Dip Hunter v2** (stream_id=2) — 1h \| RSI recovery, F&G≤20, 25% drawdown, RSI≥35, 10% trail \| $33.33
- **Breakout Scout v2** (stream_id=3) — 1h \| range_breakout \| SMA200 \| F&G≥55 \| 10% trail \| $33.33

### v3 Schema (local postgres; Supabase migration pending)
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
