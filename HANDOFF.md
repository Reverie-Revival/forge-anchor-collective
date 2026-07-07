# Handoff — 2026-07-06 (updated mid-session)

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

## Model 2 — Test Configurations

Four configs tested and saved to DB (model_id=2). **Test 4 is the current leader. Not finalizing yet.**

| Test | Config | MR | Primary v2 | Full Hist | Recent | 2026 YTD | Primary v1 |
|---|---|---|---|---|---|---|---|
| Run 1 | Config A — 4-stream $25 each | v3 staggered 7% | +17.5% | +19.4% | +18.8% | +18.3% | +22.7% |
| Run 2 | Config B — 5-stream $20 each | v3 staggered 7% | +18.6% | +19.5% | +20.2% | +12.3% | +21.3% |
| Run 3 | Config A — 4-stream $25 each | **v4 single 8%** | +19.2% | +21.7% | +20.9% | +17.8% | +25.4% |
| **Run 4** | **Config B — 5-stream $20 each** | **v4 single 8%** | **+20.0%** | **+21.4%** | **+21.9%** | +11.9% | **+26.9%** |

Run 4 = all 5 streams (VR v1, DH v3, BS v3, MR v4, SMA Pullback v1) at $20/lot each. Wins on 4 of 5 windows.
2026 YTD dip (+11.9%) is expected — SMA Pullback needs extended uptrends; choppy partial year hurts it.
MR v4 = single slot $25→$20 (removed stagger), 8% trailing stop, EMA 30/120. Saved as stream_config_id=16.

**Next step before finalizing:** Regime robustness test (see below).

---

## What's Next

### Regime Robustness Test — START HERE NEXT SESSION
Run Test 3 (Config A with MR v4) across year-by-year slices + random windows. Goal: confirm consistency across market regimes before finalizing Model 2.

Year slices to run: 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026 (partial)
Also: ~20 random 6-month windows for Monte Carlo-style consistency check.
Output: share-ready table + summary (ChatGPT/Claude review format).
Auto-save all results to DB with save_model_test(). Do not leave as pending pkl.

### Stream Lab — Results

Goal was 30%+ annual on Primary v2, or to beat Volume Raider's +26.2%.
Stream lab complete. SMA Pullback is the one candidate. Everything else rejected.

| # | Stream Name | Status | Primary v2 Result | Reason |
|---|---|---|---|---|
| — | **Capitulation Catcher** | ❌ Rejected | 43% WR, carried by 2 outlier trades | Counter-trend can't achieve high WR in BTC |
| 1 | **Volatility Breakout** | ❌ Rejected | ~14% Ann best case | ATR squeeze bug masked initial results; even fixed, couldn't match VR |
| 2 | **MACD + Volume Surge** | ❌ Rejected | 24-28% WR regardless of tuning | MACD too noisy in BTC's high-volatility env; confirmed prior failure |
| 3 | **Volume Raider 1h** | ❌ Rejected | More noise, worse signal quality | VR 4h is already the optimized version; 1h adds noise |
| 4 | **SMA Pullback** | ✅ Candidate | 37% WR, **22.9% Ann**, -23.6% MaxDD | Fills genuine gap — no Model 2 stream covers healthy-bull pullback regime |
| 5 | **Greed Rider** | ❌ Rejected | Best variant: 31% WR, +3.5% Ann | Redundant with BS+VR; F&G≥70 zone is peak-cycle danger, buys the top |

**SMA Pullback saved** as stream_id=7, config_id=15 (4h, SMA100, 3% tol, RSI<55, 15% trail, 6% SL).

**Complementarity verdict:** SMA Pullback wins. It fires in the one regime none of the 4 Model 2 streams cover (above SMA200, RSI cooling, near SMA100 bounce). Greed Rider overlaps heavily with BS v3 (both: breakout + greed sentiment) and underperforms it badly.

**If you can only pick 5 total streams:** keep the 4 Model 2 streams + SMA Pullback. Greed Rider sits out — rejected on both performance AND redundancy grounds.

### Cascade DCA v1 — NEW STREAM (stream_id=8, config_id=17)
New slot_mode `cascade` added to engine. Slots auto-enter as price falls — Slot 1 fires on signal, Slot 2+  
auto-fire when price drops `cascade_drop_pct` below previous slot's entry price.

Config v1 (config_id=17): 4h, pullback_from_high (10% drop from 48-bar high), SMA200 above, RSI<50, 5% cascade gaps, 12% trail, 15% SL, 3 slots.

Stream tester results (5 presets, test_ids 70–74):
| Window | Ann% | Trades |
|---|---|---|
| Full History | +8.4% | 60 |
| Primary Window | +16.7% | 40 |
| Primary v2 | +7.3% | 14 |
| Recent | +14.4% | 9 |
| 2026 YTD | — | 0 (no signal yet) |

**Character:** Bull market specialist. Exceptional in strong uptrends (2020 +42%, 2024 +40%). Selective — only fires on real 10% dips above SMA200. 2026 YTD = 0 trades because BTC hasn't pulled back 10% from a recent high while above SMA200 + RSI<50.

**Status:** Stream saved and viewable in Stream Tester. NOT locked for any model yet. More tuning possible — wider cascade gaps, different initial drop %, or sentiment filters.

### Possible next ideas (if you want to keep exploring)
- **Cascade DCA v2** — try 7% cascade gaps (Config H), fewer adds but fires on bigger dips. 2022 goes positive.
- **Sentiment Momentum** — enter when F&G crosses above 50 (neutral → greed transition). Not tried yet.
- **Multi-timeframe confirmation** — 4h trend + 1h entry signal. Not tried yet.

### DH regime filter — tested and closed
Adding `trend_context: above SMA200` to DH v3 kills it — only 1-2 trades ever fire. F&G≤20 (extreme fear) and price above SMA200 are nearly mutually exclusive. DH v3 stays as-is.

### When ready to deploy Model 2
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
