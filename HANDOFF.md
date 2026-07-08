# Handoff — 2026-07-08

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, cron on schedule. Trade alerting active (email + SMS on fill and close).

**Model 2 is assembled and backtested.** Run 3 selected as deployment config. Not yet deployed — at least a month away from going live.

**Model Dashboard is BUILT** — `3_model_dashboard.py` live in the multipage app (port 8504). backtest.lots seeded for Model 1 and Model 2 (multiple configs + timeframes). Feature branch merged to main.

---

## Done This Session (2026-07-08)

### Model Dashboard — built and shipped

New Streamlit page `src/app/pages/3_model_dashboard.py`. Added to `src/app/app.py` navigation.

**What it shows:**
- Portfolio snapshot: starting capital, current value, total return, annualized return (compound), YTD P&L, win rate
- Stream status cards: open positions per stream with unrealized P&L + trail stop; last closed trade summary; YTD P&L
- Equity curve: per-stream cumulative P&L lines + dotted "Avg Stream" baseline (not a sum — lets you see which streams are above/below average)
- Period tabs: All Time / YTD / 90 Days / 30 Days
- Monthly P&L breakdown table
- Open Positions: entry price, current BTC value, unrealized P&L, trail stop price, days open
- Trade Log: formatted table, sorted by close date, stream filter multiselect

**Key design points:**
- `end_of_data` exit reason = still-open simulated positions → shown in Open Positions with live BTC price
- Backtest mode uses local postgres; Live mode will use Supabase
- Annualized return uses compound formula matching `compute_metrics()` — matches the dropdown label
- Equity chart "Avg Stream" line = average of per-stream cumulative P&L at each timestamp (not sum)
- `src/app/db.py`: `get_local_engine()` always uses `DB_*` env vars for backtest schema; `get_engine()` uses `DATABASE_URL` (Supabase) for live schema. All backtest functions now call `get_local_engine()`.

### backtest.lots — seeded for all model comparisons

Seeded via Python (not the UI) using `run_model_backtest()` + `save_model_test()`:

| model_test_id | Model | Run | Window | Trades | Ann% |
|---|---|---|---|---|---|
| 97 | Model 1 | 1 | Full History | 225 | +19.4% |
| 99 | Model 2 | 3 | Full History | 218 | +21.7% |
| 100 | Model 2 | 4 | Full History | 268 | +21.4% |
| 101 | Model 1 | 4 | Full History | 142 | +22.4% |
| 102 | Model 1 | 4 | Primary v2 | 66 | +15.6% |
| 103 | Model 2 | 3 | Primary v2 | 107 | +19.4% |
| 104 | Model 2 | 4 | Primary v2 | 126 | +20.1% |

**Key insight from Primary v2 comparison (the right window):**
- Model 1: 15.6% — underperforms S&P on this window (designed pre-2022)
- Model 2 Run 3: 19.4%
- Model 2 Run 4: 20.1% — clearly the better model on the period it was designed for

**VR v1 note:** Only 6.4% annualized on Full History. Looks good on Primary v2 (26.2%) because it was designed/tuned on 2022+ data. 2018–2021 was drag. This is expected and correct.

### engine.py — highest_close added to trade records

All 6 trade close points (single/staggered/cascade × main exit + end_of_data) now include `"highest_close"` in the trade dict, enabling `high_water_mark` to be populated in `backtest.lots`.

### DB engine split — critical bug fix

`DATABASE_URL` in `.env` is the Supabase URL. Before this fix, every backtest query (`backtest.*`) was silently failing and returning empty results because `get_engine()` always used `DATABASE_URL`.

Fix: `get_local_engine()` builds connection from `DB_HOST/PORT/NAME/USER/PASSWORD` env vars (always local postgres). All backtest functions use it. `get_engine()` (Supabase) reserved for live schema only.

### Trade Alerting — built and shipped

`src/live/notifier.py` — new module. Gmail SMTP, separate sends to email and SMS so T-Mobile gateway works.

**Events:**
- **Opened** — fires when limit buy order fills (not when placed). Includes model, stream, fill price, BTC qty, capital in.
- **Closed** — fires when trailing stop triggers. Includes model, stream, entry→exit price, cash in→out, P&L.

**Setup:** 4 env vars in `.env` + 4 GitHub Actions secrets (ALERT_FROM_EMAIL, ALERT_APP_PASSWORD, ALERT_TO_EMAIL, ALERT_TO_SMS). See `.env.example`.

**Test:** `python -m src.live.notifier` — sends sample opened + closed alerts.

**SMS note:** T-Mobile rate-limits if you send many test messages in a short window. Wait ~1 hour between test bursts.

Cherry-picked to `live-model-1` — active on next real trade.

---

## What's Next

### ⚡ DATA SYNC — RUN THIS FIRST EVERY SESSION

```bash
source .venv/bin/activate
python -m src.data.downloader   # market_data (15m candles, Kraken)
python -m src.data.sentiment    # sentiment_data (F&G index)
```

### Deploy Model 2

**Selected config (as of 2026-07-08): Run 3** — Config A, 4-stream, $25/lot each.
Rationale: strongest YTD (+17.8% vs +11.9%), best Full History result, and cleaner than Run 4.
SMA Pullback v1 (the 5th stream in Run 4) showed meaningful 2026 drag — excluded for now.
This can be revisited before deployment; if no changes, Run 3 is what goes live.

Before going live:
1. ✅ All streams locked with backtest results
2. ✅ Model assembled in backtest.model_streams
3. ✅ Model backtested across multiple presets and regimes
4. ⬜ Run Supabase migration (`src/data/migration_v3.sql`) — needed for live schema
5. ⬜ Create feature branch, wire up live.models/streams, deploy executor
6. ⬜ Throw $100 at it

### Model 2 — Test Configurations

| Test | Config | MR | Primary v2 | Full Hist | Recent | 2026 YTD |
|---|---|---|---|---|---|---|
| Run 1 | Config A — 4-stream $25 each | v3 staggered 7% | +17.5% | +19.4% | +18.8% | +18.3% |
| Run 2 | Config B — 5-stream $20 each | v3 staggered 7% | +18.6% | +19.5% | +20.2% | +12.3% |
| **Run 3 ✓ SELECTED** | **Config A — 4-stream $25 each** | **v4 single 8%** | **+19.2%** | **+21.7%** | **+20.9%** | **+17.8%** |
| Run 4 | Config B — 5-stream $20 each | v4 single 8% | +20.0% | +21.4% | +21.9% | +11.9% |

Run 3 = DH v3 + VR v1 + BS v3 + MR v4, all $25/lot.
Run 4 = DH v3 + VR v1 + BS v3 + MR v4 + SMA Pullback v1, all $20/lot.

### Possible future explorations
- **Cascade DCA v2** — wider cascade gaps (7%), fewer adds but fires on bigger dips
- **Sentiment Momentum** — enter when F&G crosses above 50 (neutral → greed transition)
- **Multi-timeframe confirmation** — 4h trend + 1h entry signal

---

## Branch State

- `main` — current, all development
- `live-model-1` — production, GitHub Actions executor — DO NOT TOUCH (alerting cherry-picked here 2026-07-08)

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

### Model 2 Streams — Run 3 SELECTED (BACKTESTED, NOT LIVE)
- **Dip Hunter v3** (config_id=11): rsi_recovery 1h, SL 6%, 1 slot, $25/lot
- **Volume Raider v1** (config_id=10): volume_surge 4h, 1 slot single, $25/lot
- **Breakout Scout v3** (config_id=12): range_breakout 1h, SL 3%, 1 slot, $25/lot
- **Momentum Rider v4** (config_id=16): ema_crossover 4h, single slot, 8% trail, $25/lot

Note: SMA Pullback v1 (config_id=15) exists in DB but excluded from Run 3 — showed 2026 YTD drag.

### App Architecture
- Multipage app: `src/app/app.py` — port 8504 in dev
- Pages: Stream Tester, Model Tester, Live Monitor, Model Dashboard
- `src/app/db.py` DB pattern:
  - `get_local_engine()` — always local postgres via `DB_*` env vars — use for all `backtest.*` queries
  - `get_engine()` — uses `DATABASE_URL` (Supabase in production) — use for `live.*` queries only

### v3 Schema (local postgres; Supabase migration pending)
| Table | What it holds |
|---|---|
| `backtest.streams` | Identity only — name, strategy_type |
| `backtest.stream_configs` | Versioned params (v1/v2/etc.) + slot config |
| `backtest.model_streams` | Model composition: which config at what lot_size |
| `backtest.stream_tests` | Test results, dedup on (stream_config_id, preset_id) |
| `backtest.model_tests` | Model-level backtests; configuration JSONB, full metrics |
| `backtest.lots` | Per-trade rows for model-level tests (seeded from Python, not UI) |
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
| `db.py` | `get_engine()` routed backtest queries to Supabase silently | Split into `get_local_engine()` + `get_engine()` |
