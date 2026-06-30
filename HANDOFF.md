# Handoff — 2026-06-30

## Done

### Dip Hunter v2 — Designed, Tested, Locked

Full parameter exploration on DH starting from v1 baseline (+0.1% Primary v2).

**What worked:**
- **RSI filter min=35** — stack a filter on top of the RSI recovery signal. Signal fires when RSI crosses from below 30 to above 30, but filter requires current RSI ≥ 35. This screens "first flicker" recoveries that are still in downtrends; only entries with real momentum recovery pass through. WR jumped 38% → 55%.
- **max_hold_candles=240 (10 days on 1h)** — force-exit any trade that hasn't resolved in 10 days. Cuts stale/losing positions before DD compounds. DD dropped -32% → -20%.
- **10% trailing stop** — slightly wider than v1's 7.5%. Lets the real recoveries run.

**What was ruled out:**
- 4h timeframe — worse than 1h for DH. Fear bounces need hourly granularity to catch RSI signals cleanly.
- Wider trailing stops (12.5%, 15%) — hurt across the board when combined with scale_down or removed max_hold.
- Scale_down (2 slots) — tested exhaustively across all trigger percentages (2–15%), with/without max_hold, with looser RSI filter. Never beat single slot. The RSI filter already does the selection work scale_down was meant to accomplish through averaging.
- SMA 200 below filter — negligible effect, not worth the complexity.
- F&G threshold relaxation (25–30) — more trades, not better returns.

**Locked config — Dip Hunter v2 (stream_id=2, locked_test_id=34):**
```json
{
  "primary_timeframe": "1h",
  "core_signal": "rsi_recovery",
  "core_params": {"rsi_period": 14, "rsi_threshold": 30, "require_bullish_candle": true},
  "filters": {
    "drawdown_from_high": {"min_drop_pct": 25.0, "lookback_days": 90},
    "rsi": {"min": 35}
  },
  "sentiment": {"fear_greed": {"max": 20}},
  "position": {
    "trailing_stop_pct": 10.0,
    "entry_order_type": "limit",
    "entry_expiry_candles": 1,
    "min_hold_candles": 48,
    "max_hold_candles": 240
  }
}
```

**DH v2 results (1 slot, $10 lot):**
| Window | Ann% | Trades | WR | DD | PF |
|---|---|---|---|---|---|
| Primary v2 (2022–) | +11.7% | 20 | 55% | -20.6% | 2.03 |
| Full History (2018–) | +12.1% | 51 | 53% | -26.0% | 1.86 |
| Recent (2024–) | +10.5% | 11 | 55% | -18.6% | 2.10 |
| 2026 YTD | +53.0% | 7 | 57% | -2.2% | 2.91 |

---

### Model 1 Re-assembled with DH v2 (Run #3)

Equal $33.33/lot × 1 slot × 3 streams = $99.99 total capital.

| Window | Run #2 (MR v2 + DH v1) | Run #3 (MR v2 + DH v2) |
|---|---|---|
| Primary v2 | +8.5% | **+11.9%** |
| Full History | +16.0% | **+17.0%** |
| Recent | +9.1% | **+9.6%** |
| 2026 YTD | -6.6% | **+16.4%** |

2026 YTD swing from -6.6% → +16.4% is the headline. DH v2 now contributing where v1 was bleeding.

---

## Current DB State

**backtest.streams:**
- stream_id=1: Momentum Rider v2 — LOCKED (locked_test_id=26)
- stream_id=2: Dip Hunter v2 — LOCKED (locked_test_id=34)
- stream_id=3: Breakout Scout v1 — locked but **undertested on Primary v2 — next session**

**backtest.model_tests:**
| run | config | PV2 | FH | Recent | 2026 YTD |
|---|---|---|---|---|---|
| #1 | MR v1 + DH v1 + BS v1 | — | +5.6% | +3.6% | -16.2% |
| #2 | MR v2 + DH v1 + BS v1 | +8.5% | +16.0% | +9.1% | -6.6% |
| #3 | MR v2 + DH v2 + BS v1 | +11.9% | +17.0% | +9.6% | +16.4% |

**Stream Tester — DH v2 saved runs:**
- run#1: DH v1 baseline (old locked config, 5 presets)
- run#2: DH v2 candidate (locked config, all 5 presets including Primary Window for cross-ref)

---

## Next Up

### Priority: Tune Breakout Scout v2 for Primary v2

BS v1 was locked against Primary v1 (2019–2023) and has never been stress-tested on Primary v2. Same process as DH.

**Start here:**
1. Baseline run — BS v1 config on Primary v2:
```python
# In run_stream_test.py, set:
STREAM_NAME = "Breakout Scout v2"
PRESET_NAME = "Primary v2"
# Pull BS v1 locked config from DB:
# SELECT parameters FROM backtest.streams WHERE stream_id=3
```

2. BS uses: ATR low-vol filter + Bollinger squeeze + F&G > 50. Check if the squeeze and vol conditions fire in the 2022+ regime.
3. If trade count is low: relax entry conditions (ATR threshold, squeeze threshold)
4. If trades are there but returns low: widen stop, adjust F&G floor
5. Scale_down tested for DH and didn't help — worth trying for BS (breakout continuation is a better fit for pyramiding)
6. Run any improvements across Full History + Recent to verify generalization
7. Save to DB, review in Stream Tester, lock

**Batch exploration template:** copy `src/backtester/explore_dh_v2b.py` — same structure, adapt signal/filter keys for BS

**Target:** BS v2 strong enough that combined model clears 15%+ on Primary v2 and 18%+ on Full History.

---

## Architecture Reference

| File | Purpose |
|---|---|
| `src/backtester/engine.py` | Core backtest engine — `_run_slot()`, slot_mode dispatch, `_warmup_days()` |
| `src/backtester/signals.py` | `generate_signals()` + `_check_filters()` — all signal/filter logic lives here |
| `src/backtester/model_runner.py` | `run_model()` — loads locked streams from DB, applies allocations |
| `src/backtester/run_stream_test.py` | Quick single-config runner → writes .last_run.pkl for Stream Tester |
| `src/backtester/explore_dh_v2.py` | DH v2 Round 1 exploration (timeframe × stop × F&G × hold) |
| `src/backtester/explore_dh_v2b.py` | DH v2 Round 2 exploration (RSI filter, max_hold, SMA regime) |
| `src/app/db.py` | All DB ops — `save_stream_test()`, `save_model_test()`, `get_engine()` |
| `src/app/pages/model_tester.py` | Model Tester page |
| `src/app/model_dashboard.py` | Model dashboard renderer |

**Run Streamlit:** `streamlit run src/app/app.py`

**Available signals in engine:** `ema_crossover`, `rsi_recovery`, `rsi_dip`, `range_breakout`, `volume_surge`, `fear_dip`, `sma_pullback`
**Available filters:** `trend_context` (SMA above/below), `rsi` (min/max), `drawdown_from_high`, `sentiment.fear_greed`, `volume`, `atr_regime`, `bollinger`, `breakout_candle`
