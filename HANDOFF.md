# Handoff — 2026-06-30

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

## Next Up

### Priority: Deployment Decision

Model 1 passes all backtesting gates:
- +15.3% annualized on Primary v2 (beats S&P 500 10% target)
- All 5 windows positive
- Max DD -12.8% on Primary v2
- Zero signal overlap between streams confirmed

**Before going live:**
1. Review Kraken API integration — fee structure, order placement, WebSocket for live prices
2. Build `live.models`, `live.streams`, `live.lots` schema and execution engine
3. Staggered slot redesign (can be done before or after live deployment — single slot is deployable as-is)
4. Connect live reporting to `reporting.all_lots` view

### Secondary: Staggered Slot Engine Redesign
Open a feature branch. Change is in `src/backtester/engine.py` → `_run_slot()`. Will require updating stream params schema and stream spec docs.

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
