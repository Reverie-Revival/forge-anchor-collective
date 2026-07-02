# Handoff — 2026-06-30 (updated)

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---


## Done

### Breakout Scout v2 — Tuned, Tested, Locked

Full parameter exploration on BS starting from v1 baseline (-1.8% Primary v2, PF 0.64).

**What worked:**
- **Breakout lookback 24h (from 48h)** — the primary unlock. 48h required breaking a 2-day range; 24h catches breakouts earlier with better reward/risk. Went from 11 to 23 trades on Primary v2.
- **10% trailing stop (from 5%)** — lets genuine breakout continuation run. 5% was cutting winners before they realized.
- **SMA 200 above filter** — breakouts only in macro uptrend. Added +0.7% return AND reduced DD simultaneously (counterintuitive). Gives BS a distinct identity: bull-regime breakout play, the opposite of DH's bear-regime role.
- **F&G ≥ 55 (raised from 50)** — with the 24h lookback doing more work, tightening sentiment to greedy-only improved PF and Recent performance.

**What was ruled out:**
- `max_hold` — unlike DH, BS winners need to run. Force-exiting at 5–14 days consistently destroyed returns.
- Loosening ATR filter — ATR at 90% is the real quality gate. Removing it flooded bad trades (75 trades, 33% WR, -11.7%).
- Bollinger squeeze threshold tuning — irrelevant. Removing the squeeze filter entirely produced identical results. ATR does all the gating.
- scale_up (2 slots at current engine behavior) — current implementation enters both slots on same signal. Deferred to staggered-slot redesign.

**Locked config — Breakout Scout v2 (stream_id=3, locked_test_id=35):**
```json
{
  "primary_timeframe": "1h",
  "core_signal": "range_breakout",
  "core_params": {"breakout_lookback": 24},
  "filters": {
    "bollinger": {"period": 20, "std_dev": 2.0, "squeeze": {"max_bandwidth_pct": 6.0}},
    "atr_regime": {"period": 14, "avg_period": 30, "max_pct_of_avg": 90},
    "breakout_candle": {"body_ratio_min": 0.4, "close_position_min": 0.6},
    "trend_context": {"sma_period": 200, "require": "above"}
  },
  "sentiment": {"fear_greed": {"min": 55}},
  "position": {
    "trailing_stop_pct": 10.0,
    "entry_order_type": "limit",
    "entry_expiry_candles": 2
  }
}
```

**BS v2 results (1 slot, $10 lot):**
| Window | Ann% | Trades | WR | DD | PF |
|---|---|---|---|---|---|
| Primary v2 (2022–) | +11.6% | 16 | 31% | -25.3% | 1.78 |
| Full History (2018–) | +25.1% | 41 | 39% | -27.8% | 2.11 |
| Recent (2024–) | +16.6% | 12 | 33% | -25.3% | 1.84 |
| 2026 YTD | 0 trades | — | — | — | — |
| Primary Window (2019–2023) | +36.2% | 26 | 42% | -27.8% | 2.54 |

2026 YTD = 0 trades is correct — BS needs F&G ≥ 55 + price above SMA 200. 2026 is a fear-dominated market; DH covers that regime.

---

### Model 1 Re-assembled — Run #4 (all v2 streams)

Equal $33.33/lot × 1 slot × 3 streams = $99.99 total capital.

| Window | Run #3 (MR v2 + DH v2 + BS v1) | Run #4 (all v2) |
|---|---|---|
| Primary v2 | +11.9% | **+15.3%** |
| Full History | +17.0% | **+22.2%** |
| Recent | +9.6% | **+14.7%** |
| 2026 YTD | +16.4% | **+16.4%** |
| Max DD (PV2) | — | **-12.8%** |

Model DD of -12.8% on Primary v2 is tighter than any individual stream's standalone DD — diversification compressing risk as intended.

---

### Allocation Exploration

Tested 10 allocation splits from equal $33 to MR $60 / BS $25 / DH $15.

**Finding:** Returns climb linearly with MR weight, but every dollar shifted from DH costs 2026 YTD performance (DH is the active stream in the current fear regime). MR $60 / DH $10 reaches +17.9% PV2 but drops to +4.9% YTD.

**Decision:** Keep equal $33.33 allocation for Run #4 deployment. Preserves regime responsiveness across all market conditions.

---

## Current DB State

**backtest.streams:**
- stream_id=1: Momentum Rider v2 — LOCKED (locked_test_id=26)
- stream_id=2: Dip Hunter v2 — LOCKED (locked_test_id=34)
- stream_id=3: Breakout Scout v2 — LOCKED (locked_test_id=35)

**backtest.stream_tests (BS v2 saves):**
- test_id=35: BS v2 | Primary v2 | +11.6% (locked)
- test_id=36: BS v2 | Full History | +25.1%
- test_id=37: BS v2 | Recent | +16.6%
- test_id=38: BS v2 | 2026 YTD | 0 trades
- test_id=39: BS v2 | Primary Window | +36.2%

**backtest.model_tests (Run #4):**
| model_test_id | preset | ann% | DD |
|---|---|---|---|
| 15 | Primary v2 | +15.3% | -12.8% |
| 16 | Full History | +22.2% | -15.8% |
| 17 | Recent | +14.7% | -13.6% |
| 18 | 2026 YTD | +16.4% | -2.8% |
| 19 | Primary Window | +30.7% | -13.9% |

---

## Design Decisions Logged

### Staggered Slot Architecture (not yet built)

Current `scale_up`/`scale_down` slot behavior is NOT the intended design. What we want:
- Two independent capital buckets per stream
- Slots cannot enter the same signal simultaneously — Slot 2 must wait for the NEXT signal
- Slots alternate freely based on availability
- Optional configurable wait time before Slot 2 becomes eligible

Best fit for DH (sequential fear dips) and BS (consecutive squeeze breakouts). MR less useful since EMA crossovers in the same direction don't repeat quickly.

This is an engine-level change that requires a feature branch before touching `engine.py`.

---

## Roadmap

| Priority | Path | Notes |
|---|---|---|
| **1** | **Deploy Model 1 Live** | Kraken API integration, `live.*` schema, execution engine. Model 1 has passed all gates — this is the mission. |
| **2** | **Architecture & Database Remodel** | Third full remodel — DB schema + code restructure. Scope after seeing live operation pain points. |
| **2** | **Live Dashboard** | Streamlit monitoring for the live model — P&L, trade log, stream breakdown, benchmarks. Depends on live deployment; likely runs concurrently with architecture work. |
| — | **Stream/Model Tester Fixes** | Known UI issues in Stream Tester and Model Tester. Address opportunistically or bundle into architecture remodel. |
| — | **Staggered Slot Redesign** | Engine-level feature branch. Makes multi-slot streams actually useful (sequential entries vs. duplicate). Benefits DH + BS most. |
| — | **Build Model 2** | Overlap phase — start once live is stable and running. |

---

## Deploy Model 1 Live — Status

### Code complete (on `live-model-1` branch)

| File | Purpose |
|---|---|
| `src/live/kraken_client.py` | Authenticated REST wrapper — place_order, cancel_order, get_order_status, get_balance, get_ticker_price |
| `src/live/deploy.py` | One-time script — creates live.models row + copies streams at $33.33/lot |
| `src/live/signal_engine.py` | Runs locked signal logic on latest market_data; returns True/False per stream |
| `src/live/order_manager.py` | CASH→PENDING→OPEN→CLOSED state machine; entry limit orders + market exit |
| `src/live/position_monitor.py` | Trailing stop monitor; fires on candle close per stream's timeframe |
| `src/live/executor.py` | Single-invocation tick (GitHub Actions); `--dry-run` flag skips Kraken orders but writes DB |
| `src/live/market_data_updater.py` | GitHub Actions cron: fetches last 2h of 15-min candles from Kraken public OHLC API, upserts to Supabase |
| `tests/live/test_signal_parity.py` | Layer 1 parity tests — 3/3 passing; reads locked configs from DB |
| `.github/workflows/executor.yml` | Runs executor every 30 min via GitHub Actions |
| `.github/workflows/market_data.yml` | Fetches market data every 15 min via GitHub Actions |

### Schema changes applied (schema.sql)
- `live.lots.status`: added `PENDING` state (CASH → PENDING → OPEN → CLOSED)
- `live.lots.entry_expiry_at`: new column — timestamp when limit order should be cancelled if unfilled

### What still needs to happen before real money

**Hosting architecture: GitHub Actions + Supabase ($0/month)**
- GitHub Actions runs two workflows: executor every 30 min, market data updater every 15 min
- Supabase free PostgreSQL holds live schema + 60 days of market_data only
- Minimum candle timeframe: 30 minutes (15m streams would miss closes at 30-min executor interval)

**User actions required:**
1. Set GitHub $0 spending cap — Settings → Billing → Spending limits → Actions → $0
2. Create Supabase account at supabase.com with personal email
3. Come back for help: apply live schema to Supabase, seed 60 days market_data + sentiment_data, add GitHub Secrets
4. Create Kraken Pro account → generate API key (Create Order permission only, NO withdrawal)

**Code steps (after infrastructure):**
5. Push `live-model-1` branch — workflows activate automatically on schedule
6. Run `python -m src.live.deploy` pointed at Supabase — creates live.models + live.streams rows
7. Set `DRY_RUN=true` GitHub Secret → monitor Actions logs 24h — verify signal detection and DB state machine
8. Remove dry-run secret → deploy full $100

### Fee note
Round trip live = 0.65% (0.25% limit entry + 0.40% market exit) vs. 0.50% assumed in backtest. ~$0.80 total delta over all trades. Known and acceptable.

### Branch strategy
- `main` — all development (testers, arch remodel, Model 2). Never deployed directly.
- `live-model-1` — frozen at this deployment. Runs via GitHub Actions.
- Bug fixes: patch `live-model-1`, cherry-pick to `main`.
- Model 2: new `live-model-2` branch from `main` when it passes its backtest gate.

---

## Architecture Reference

| File | Purpose |
|---|---|
| `src/backtester/engine.py` | Core backtest engine — `_run_slot()`, slot_mode dispatch, `_warmup_days()` |
| `src/backtester/signals.py` | `generate_signals()` + `_check_filters()` — all signal/filter logic |
| `src/backtester/model_runner.py` | `run_model()` — loads locked streams from DB, applies allocations |
| `src/backtester/run_stream_test.py` | Quick single-config runner → writes .last_run.pkl for Stream Tester |
| `src/backtester/explore_bs_v2.py` | BS v2 Round 1 (stop width, entry relaxation, F&G, lookback) |
| `src/backtester/explore_bs_v2b.py` | BS v2 Round 2 (max_hold, SMA regime, lookback combos, scale_up) |
| `src/backtester/explore_bs_v2c.py` | BS v2 Round 3 (SMA on CandA, CandB refinements, scale_up on top R2 configs) |
| `src/backtester/explore_allocation.py` | Model 1 allocation splits (equal vs MR-heavy) |
| `src/backtester/run_model_r4.py` | One-shot script that locked BS v2 and saved Run #4 |
| `src/app/db.py` | All DB ops — `save_stream_test()`, `save_model_test()`, `get_engine()` |
| `src/app/pages/model_tester.py` | Model Tester page |
| `src/app/model_dashboard.py` | Model dashboard renderer (stream colors defined here) |

**Run Streamlit:** `streamlit run src/app/app.py`

**Available signals in engine:** `ema_crossover`, `rsi_recovery`, `rsi_dip`, `range_breakout`, `volume_surge`, `fear_dip`, `sma_pullback`
**Available filters:** `trend_context`, `rsi`, `drawdown_from_high`, `sentiment.fear_greed`, `volume`, `atr_regime`, `bollinger`, `breakout_candle`
