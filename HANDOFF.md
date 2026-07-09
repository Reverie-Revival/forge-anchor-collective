# Handoff — 2026-07-09

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, cron on schedule. Full alert coverage active (order placed, filled, closed, expired, system down).

**Model 2 is assembled and backtested.** Run 3 selected as deployment config. Not yet deployed — at least a month away from going live.

**Model Dashboard is BUILT** — `3_model_dashboard.py` live in the multipage app (port 8504).

---

## Done This Session (2026-07-09)

### Alert Coverage — expanded and hardened

**New alert types in `src/live/notifier.py`:**
- `alert_order_placed()` — fires when limit buy hits Kraken, before fill. Includes expiry time so you know the window.
- `alert_order_expired()` — fires when an order times out unfilled or Kraken cancels it. Slot freed automatically.
- `alert_system_down(hours)` — fires when executor has been silent > 2h.

**Full alert sequence:**
1. Order Placed — limit buy submitted to Kraken
2. Opened — limit buy filled
3. Closed — trailing stop triggered
4. Order Expired — order never filled, slot freed

**Test command:** `python -m src.live.notifier` — sends all 4 trade alert types.
**SMS note:** T-Mobile rate-limits burst sends. Wait ~1h between test runs.

### Dead Man's Switch — two layers

**Layer 1 (self-check in executor.py):** When executor runs, if gap from `last_run_at` > 2h, fires `alert_system_down`. Catches "system recovered after outage."

**Layer 2 (independent healthcheck):** `src/live/healthcheck.py` + `.github/workflows/healthcheck.yml`. Separate cron-job.org schedule (every 2h) triggers this independently of the executor. Queries `live.executor_state` and alerts if stale. Catches ongoing outages the executor itself can't detect.

**cron-job.org setup:** A second job was added pointing at the `healthcheck` workflow. Same PAT, same pattern as executor/market_data jobs.

### Order Expiry Fix — `order_manager.check_pending()`

**Before:** Expiry check ran AFTER Kraken API call. If Kraken was unreachable, `continue` skipped the expiry check — expired lots could pile up indefinitely, blocking slots.

**After:** Expiry check runs FIRST based on our own `entry_expiry_at` timestamp. Expired lots are always cancelled and deleted regardless of Kraken API health. Kraken query only runs for non-expired lots.

Also added `entry_price` to the `check_pending` SELECT (was missing, needed for expiry alert).

### Cherry-pick to live-model-1

All changes cherry-picked. Conflict resolved: `live-model-1` had a `_preflight_check()` (data freshness gate) not in `main` — preserved it and slotted the gap check before it. Both layers now on both branches.

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

### Future Explorations (spitballed this session, nothing to build yet)

- **ETH as next asset (Model 3):** ETH/USD on Kraken, same fee structure, same strategy types. Main lift: make `pair` first-class in executor + backtester. Recommended: pure ETH-only $100 model first before mixing assets.
- **SOL:** Higher beta, thinner liquidity. Valid but wait until ETH has real data.
- **Mixed-asset models:** BTC + ETH + SOL streams in one model. Interesting long-term.
- **Multi-account support:** Let a family member run the same model on their own Kraken account. ~1 day of work. Gate: Model 1 needs 3-6 months live track record first. Requires balance check + low-balance alert alongside it.

---

## Branch State

- `main` — current, all development
- `live-model-1` — production, GitHub Actions executor — cherry-pick critical fixes only

## Pending: Supabase Migration

`src/data/migration_v3.sql` has NOT been run on Supabase yet. Only needed when deploying Model 2.
Live executor uses `live.*` schema only — not affected.

---

## Reference: Architecture

### Branch Strategy
- `main` — all development. Dashboard, backtester, all 4 Streamlit pages.
- `live-model-1` — production only. Critical fixes only. No feature work.
- Bug fixes to live: commit to `live-model-1` directly, cherry-pick to `main`
- **Important:** `live-model-1` has `_preflight_check()` in executor.py (data freshness gate) that main does not. Don't lose it on future cherry-picks.

### GitHub Actions Workflows
| Workflow | Trigger | What It Does |
|---|---|---|
| `executor.yml` | Every 30 min (cron-job.org) | Runs `src.live.executor` tick |
| `market_data.yml` | Every 15 min (cron-job.org) | Fetches candles + updates sentiment |
| `healthcheck.yml` | Every 2h (cron-job.org) | Dead man's switch — alerts if executor silent > 2h |

### Alert System (`src/live/notifier.py`)
| Function | When It Fires |
|---|---|
| `alert_order_placed()` | Limit buy submitted to Kraken |
| `alert_opened()` | Limit buy filled |
| `alert_closed()` | Trailing stop triggered, position closed |
| `alert_order_expired()` | Order timed out unfilled, slot freed |
| `alert_system_down(hours)` | Executor silent > 2h (executor self-check or healthcheck) |

### Model 1 Streams (LIVE)
- **Momentum Rider v2** (stream_id=1) — 4h | EMA 30/120 | 7% trail | $33.33
- **Dip Hunter v2** (stream_id=2) — 1h | RSI recovery, F&G≤20, 25% drawdown, RSI≥35, 10% trail | $33.33
- **Breakout Scout v2** (stream_id=3) — 1h | range_breakout | SMA200 | F&G≥55 | 10% trail | $33.33

### Model 2 Streams — Run 3 SELECTED (BACKTESTED, NOT LIVE)
- **Dip Hunter v3** (config_id=11): rsi_recovery 1h, SL 6%, 1 slot, $25/lot
- **Volume Raider v1** (config_id=10): volume_surge 4h, 1 slot single, $25/lot
- **Breakout Scout v3** (config_id=12): range_breakout 1h, SL 3%, 1 slot, $25/lot
- **Momentum Rider v4** (config_id=16): ema_crossover 4h, single slot, 8% trail, $25/lot

### App Architecture
- Multipage app: `src/app/app.py` — port 8504 in dev (`streamlit run src/app/app.py --server.port 8504`)
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
| `order_manager.py` | Expiry check ran after Kraken API call — expired lots stuck if Kraken unreachable | Check our own expiry timestamp first |
