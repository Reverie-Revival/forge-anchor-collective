# Handoff — 2026-07-06 (end of session)

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, cron on schedule.

**Model 2 is assembled and backtested.** 4 streams locked, model registered in DB, 5-preset backtest run and saved. Not yet deployed — at least a month away from going live.

---

## Done This Session (2026-07-06)

### Stream Tester — Risk Metrics + new charts
- **Risk Metrics section** added to stream dashboard: Sharpe, Sortino, Calmar, Avg MAE, Avg MFE, Max Consec. Losses
- **MAE/MFE tracking** added to engine.py — every trade record now carries `mae_pct` and `mfe_pct` (lowest_low and highest_high tracked per candle)
- **On-the-fly metrics patch** in `load_run_payload` — old pkls missing new metric keys get them recomputed from stored trades on load (no re-run required)
- **Monthly return heatmap** — full-width green/red calendar grid per year × month
- **Return distribution histogram** — green/red overlaid, median line
- **MAE vs MFE scatter** — each trade as a dot, hard stop + trail stop reference lines. Shows `—` for old runs; Re-run All Presets populates it.
- **Wide layout** set on both stream_tester.py and model_tester page
- **Re-run All Presets button** added (↺) — forces fresh backtest even if preset already saved

### Model 2 — fully assembled
4 streams locked in `backtest.stream_configs`:

| Stream | config_id | Slots | lot_size_usd | Total | Key params |
|---|---|---|---|---|---|
| Volume Raider v1 | 10 | 1 single | $25.00 | $25 | volume_surge 4h, RSI 30-60, 10% trail |
| Dip Hunter v3 | 11 | 1 single | $25.00 | $25 | rsi_recovery 1h, SL 6%, 10% trail |
| Breakout Scout v3 | 12 | 1 single | $25.00 | $25 | range_breakout 1h, SL 3%, 10% trail |
| Momentum Rider v3 | 9 | 2 staggered [70/30] | $12.50 | $25 | ema_crossover 4h, 7% trail |
| **Total** | | | | **$100** | |

Model registered as model_id=2 in `backtest.models`. Composition in `backtest.model_streams`.

**Model 2 backtest results (model_test_ids 20–24):**

| Window | Ann% | Trades | DD% | WR |
|---|---|---|---|---|
| Full History (2018→) | +19.4% | 225 | -14.1% | 43% |
| Primary Window (2019→2023) | +22.7% | 124 | -10.4% | 42% |
| Primary v2 (2022→now) | +17.5% | 111 | -11.8% | 46% |
| Recent (2024→now) | +18.8% | 67 | -11.5% | 48% |
| 2026 YTD | +18.3% | 13 | -2.9% | 69% |

Grade 4 (Strong) across all windows. Beats BTC buy-and-hold (+6.7% same period) and S&P avg.

Per-stream on Primary v2: VR +26.2%, BS +16.8%, DH +12.7%, MR +12.4%.
VR is the standout performer — runs better alone but the group provides drawdown control and regime diversity.

### Model Tester — multi-model support
- Model selector dropdown added to sidebar — switches between Model 1 and Model 2
- `load_models()` added to db.py; `load_model_history(model_id)` now filters by model
- `model_runner.py` fixed to load from v3 schema (`backtest.model_streams` → `backtest.stream_configs`) instead of old `backtest.streams` table
- `STREAM_COLORS` in model_dashboard.py updated with all v3 stream versions (MR, DH, BS all shades of their family color; VR = pink)

### ADX indicator added (not used in production)
- `adx()` added to `indicators.py` — Wilder smoothing, correct +DM/-DM zero-out
- Filter wired into `signals.py` (`adx` filter key) and `engine.py` (warmup days = period × 3)
- Tested on MR v3: ADX≥20 improves win rate (44%→50%) and drawdown but costs -2.8% annualized on Primary v2. Not worth the trade. MR v3 kept as-is.

---

## Model 2 Deployment Status

Model 2 is NOT deployed. Design and backtesting complete. Before going live:
1. ✅ All 4 streams locked with backtest results in stream_tests
2. ✅ Model assembled in backtest.model_streams
3. ✅ Model backtested across 5 presets (model_tests)
4. ⬜ Run Supabase migration (`src/data/migration_v3.sql`) — needed before live schema exists
5. ⬜ Create feature branch, wire up live.models/streams, deploy executor
6. ⬜ Throw $100 at it — at least 1 month from now

---

## What's Next

### High priority
- **DH regime filter** — test `trend_context: above 200 SMA` on DH v3. Only buy dips in bull markets. Quick test, might improve the weakest stream. If it helps → DH v4, re-run model backtest.
- **5th stream design** — SMA Pullback is the top candidate (`sma_pullback` core signal already exists). Would diversify Model 2 or seed Model 3. Not urgent.

### When ready to deploy
- Run Supabase migration, create feature branch, deploy Model 2 with $100

---

## Branch State

- `main` — current, all development
- `live-model-1` — production, GitHub Actions executor — DO NOT TOUCH
- No active feature branches

## Pending: Supabase Migration

`src/data/migration_v3.sql` has NOT been run on Supabase yet. Only needed when deploying Model 2.
Live executor uses `live.*` schema only — not affected.

---

## Reference: Architecture

### Branch Strategy
- `main` — all development
- `live-model-1` — production only. Critical fixes only. No feature work.
- Bug fixes to live: commit to `live-model-1` directly, cherry-pick to `main`

### GitHub Actions Workflows
| Workflow | Trigger | What It Does |
|---|---|---|
| `executor.yml` | Every 30 min | Runs `src.live.executor` tick |
| `market_data.yml` | Every 15 min | Fetches candles + updates sentiment |

### Model 1 Streams (LIVE)
- **Momentum Rider v2** (stream_id=1) — 4h | EMA 30/120 | 7% trail | $33.33
- **Dip Hunter v2** (stream_id=2) — 1h | RSI recovery, F&G≤20, 25% drawdown, RSI≥35, 10% trail | $33.33
- **Breakout Scout v2** (stream_id=3) — 1h | range_breakout | SMA200 | F&G≥55 | 10% trail | $33.33

### Model 2 Streams (BACKTESTED, NOT LIVE)
See stream table above. model_id=2 in backtest.models.

### v3 Schema (local postgres; Supabase migration pending)
| Table | What it holds |
|---|---|
| `backtest.streams` | Identity only — name, strategy_type |
| `backtest.stream_configs` | Versioned params (v1/v2/etc.) + slot config |
| `backtest.model_streams` | Model composition: which config at what lot_size |
| `backtest.stream_tests` | Test results, dedup on (stream_config_id, preset_id) |
| `backtest.model_tests` | Model-level backtests; configuration JSONB, full metrics |
| `backtest.models` | Model version registry (model_id, model_version, status) |
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
