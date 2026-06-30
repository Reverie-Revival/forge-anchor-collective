# Handoff

## Last session: 2026-06-29 (session 3)

---

## What was completed

### Model Tester — built end to end

Full model-level testing infrastructure added:

**New files:**
- `src/backtester/model_engine.py` — runs all locked streams simultaneously, aggregates combined metrics
- `src/backtester/model_runner.py` — programmatic entry point; call from Claude Code to trigger a run and write pkl to model_runs/
- `src/app/model_dashboard.py` — full dashboard: combined KPIs, benchmark tiles, stream breakdown, racing lines (balance over time), combined equity curve with solo-stream overlays, drawdown chart, trade log, save button
- `src/app/app.py` — multi-page Streamlit app entry point (Stream Tester + Model Tester)
- `src/app/pages/model_tester.py` — Model Tester page: glossary, stream reference expander, sidebar run selector, main dashboard area

**Key dashboard features:**
- Racing lines chart — per-stream balance over time (not % return), each stream in its own color
- Combined Account Balance chart — combined equity curve with toggleable solo-stream overlays (click legend); shows "what if $100 went into just one stream" vs. combined
- Benchmark tiles — vs. S&P 500 historical avg, S&P 500 actual for the period, BTC buy-and-hold
- Save to DB — saves run to `backtest.model_tests`; run_number groups same-allocation configs across windows
- Pending pkl system — unsaved runs appear immediately in UI without saving; saved runs load from pkl + DB

**Dollar sign LaTeX fix:** Streamlit treats `$...$` as LaTeX. All currency values in `st.caption()` now use `\$` escape.

### Model 1 assembled and tested

Allocation: **$16.67/lot × 2 slots × 3 streams = $100.02 total** (equal-split across 3 streams).

To re-run any window:
```python
from src.backtester.model_runner import run_model

result = run_model(
    allocations={
        'Momentum Rider v1': {'lot_size_usd': 16.67, 'slot_count': 2},
        'Dip Hunter v1':     {'lot_size_usd': 16.67, 'slot_count': 2},
        'Breakout Scout v1': {'lot_size_usd': 16.67, 'slot_count': 2},
    },
    start='YYYY-MM-DD',
    end='YYYY-MM-DD',
)
```

**Results saved to DB (Run #1):**

| Window | Period | Trades | Ann. Return | Win Rate | Max DD |
|---|---|---|---|---|---|
| Primary | 2019–2023 | — | +12.75% | — | — |
| Full History | 2018–2026 | — | +5.57% | — | — |
| Recent | 2026 YTD | 30 | -16.0% | 26.7% | -9.3% |

2026 context: BTC -54% YTD. Model at -16% — significantly outperforms buy-and-hold.

---

## Previous sessions

### Session 2 (2026-06-29): Breakout Scout v1 — locked

`stream_id=3, locked_test_id=14`

```python
params = {
    'primary_timeframe': '1h',
    'core_signal': 'range_breakout',
    'core_params': {'breakout_lookback': 48},
    'filters': {
        'atr_regime': {'period': 14, 'avg_period': 30, 'max_pct_of_avg': 90},
        'bollinger': {'period': 20, 'std_dev': 2.0, 'squeeze': {'max_bandwidth_pct': 6.0}},
        'breakout_candle': {'body_ratio_min': 0.4, 'close_position_min': 0.6},
    },
    'sentiment': {'fear_greed': {'min': 50}},
    'position': {'trailing_stop_pct': 5.0, 'entry_order_type': 'limit', 'entry_expiry_candles': 2},
}
```

| Window | Trades | Ann. Return | Win Rate | PF | Max DD |
|---|---|---|---|---|---|
| Primary (2019–2023) | 36 | +13.0% | 61.1% | 2.51 | -14.1% |
| Full History (2018–2026) | 60 | +6.4% | 56.7% | 1.83 | -18.7% |
| Recent (2026 YTD) | 0 | — | — | — | — |

### Session 1 (2026-06-29): Dip Hunter v1 — locked

`stream_id=2, locked_test_id=10`

| Window | Trades | Ann. Return | Win Rate | PF | Max DD |
|---|---|---|---|---|---|
| Primary (2019–2023) | 78 | +17.5% | 46.2% | 1.43 | -32.1% |
| Full History (2018–2026) | 150 | +8.9% | 44.0% | 1.34 | -32.1% |
| 2026 YTD | 22 | -18.5% | 36.4% | 0.67 | -20.4% |

---

## Current state

**Model 1 — complete, results in DB**

| # | Stream | Grade | Status |
|---|---|---|---|
| 1 | Momentum Rider v1 | 4 — Strong | Locked ✓ (stream_id=1) |
| 2 | Dip Hunter v1 | 4 — Strong | Locked ✓ (stream_id=2) |
| 3 | Breakout Scout v1 | 4 — Strong | Locked ✓ (stream_id=3) |

`backtest.stream_tests` — 16 rows. `backtest.model_tests` — 3 rows (Run #1, all windows).

---

## What's next

### 1. Discuss Model 1 results
- Primary window: +12.75% — beats S&P historical avg (~10%)
- Full History: +5.57% — below S&P, but 2026 bear is dragging it down
- 2026 YTD: -16% vs. BTC buy-and-hold -54%
- Decision: is this deployment-ready? Or try an allocation adjustment first?

### 2. If deploying Model 1
- Update `backtest.model_tests` `selected_for_deployment = TRUE` on the Primary window row
- Begin Kraken API integration (live schema not yet built)

### 3. Model 2 overlap phase
- While Model 1 runs live, begin designing Model 2
- Consider stronger bear-market coverage — DH underperformed in 2026's grinding bear

---

## Key files

| File | Purpose |
|---|---|
| `src/backtester/model_engine.py` | Run all streams simultaneously |
| `src/backtester/model_runner.py` | Entry point — call this from Claude Code |
| `src/app/app.py` | Multi-page Streamlit entry point |
| `src/app/model_dashboard.py` | All dashboard charts and save logic |
| `src/app/pages/model_tester.py` | Model Tester page |
| `src/app/db.py` | All DB ops (stream tests + model tests) |
| `src/backtester/engine.py` | Single-stream backtest engine |
| `docs/architecture/database-schema.md` | Full schema reference |
| `docs/architecture/stream-attributes.md` | All configurable stream params |
