# Handoff

## Last session: 2026-06-29

---

## What was completed

### Dip Hunter v1 — designed, iterated, and locked

Full design journey this session:

- Built `rsi_recovery` core signal: fires on the specific candle where RSI crosses back UP through threshold (prev < 30, curr >= 30). Enters the bounce, not the continuous dip.
- Built `drawdown_from_high` filter: requires price to have dropped ≥ N% from its N-day high. Configurable `lookback_days` + `min_drop_pct`. Uses pre-start warmup data.
- Added `require_bullish_candle` option to `rsi_recovery`: also requires close > prev_close on entry candle. Adds second confirmation the bounce is actually happening.
- Iterated to profitability on Primary: `rsi_dip` → `rsi_recovery` (win rate 29% → 46%) → added `min_hold_candles: 48` → added bullish candle filter (Full History +0.8% ann).
- Confirmed complementarity: MR fires in bull (F&G>25, above 200 SMA, RSI>55). DH fires in bear/extreme fear (F&G<20, 25%+ drawdown from 90d high, RSI recovering). Zero signal overlap by design.

**Locked config — Dip Hunter v1 (stream_id=2, locked_test_id=10):**
```python
params = {
    'primary_timeframe': '1h',
    'core_signal': 'rsi_recovery',
    'core_params': {
        'rsi_period': 14,
        'rsi_threshold': 30,
        'require_bullish_candle': True,
    },
    'filters': {
        'drawdown_from_high': {'lookback_days': 90, 'min_drop_pct': 25.0}
    },
    'position': {
        'trailing_stop_pct': 7.5,
        'entry_order_type': 'limit',
        'entry_expiry_candles': 1,
        'min_hold_candles': 48,
    },
    'sentiment': {'fear_greed': {'max': 20}}
}
```

| Window | Trades | Ann. Return | Win Rate | PF | Max DD |
|---|---|---|---|---|---|
| Primary (2019–2023) | 78 | +17.5% | 46.2% | 1.43 | -32.1% |
| Full History (2018–2026) | 150 | +8.9% | 44.0% | 1.34 | -32.1% |
| 2026 YTD (bear) | 22 | -18.5% | 36.4% | 0.67 | -20.4% |

2026 result context: BTC was -54% YTD. DH still meaningfully outperforms buy-and-hold. The loss reflects a grinding bear with no clean dip+recovery cycles — not a strategy flaw.

### Indicator warmup — fixed systemically

`engine.py` now loads pre-start warmup data for every run. `_warmup_days(params)` computes the required lookback from all active indicators/filters. Data is loaded from `start - warmup_days`, indicators computed on the full dataset, then clipped to `start` before signals run. Day 1 of any window now has correct values for all rolling indicators (drawdown_from_high, SMA, ATR, etc.).

### Stream Tester — refactored and features added

Split `stream_tester.py` (1083 lines) into four focused modules:
- `src/app/utils.py` — pure helpers (params_hash, label_window, grade_info, compact config, human-readable description)
- `src/app/db.py` — all DB operations and path constants
- `src/app/dashboard.py` — `render_dashboard` only
- `src/app/stream_tester.py` — page layout, glossary, sidebar, main area (432 lines)

New **Parameter Reference** expander in the app — shows every configurable attribute (core signals, filters, position params) with descriptions and valid values. Good reference when designing new streams.

Fixed S&P 500 benchmark: yfinance now returns MultiIndex columns — flattened before use.

Fixed params hash normalization: `_strip_none()` removes null fields before hashing so `{"a": 1, "b": null}` and `{"a": 1}` hash identically. Re-running a saved config with slightly different null handling now correctly groups as the same run.

Fixed `__new__` tab rendering: unsaved runs with new params now show all pending windows as tabs (not just the single latest run).

---

## Current state

**Model 1 — in progress**

| # | Stream | Grade | Status |
|---|---|---|---|
| 1 | Momentum Rider v1 | 4 — Strong | Locked ✓ (stream_id=1) |
| 2 | Dip Hunter v1 | 4 — Strong | Locked ✓ (stream_id=2) |
| 3 | Breakout Scout v1 | — | Not started |
| 4 | Steady Climber v1 | — | Not started |
| 5 | Surge Rider v1 | — | Not started |

Note: Model 1 doesn't need all 5 streams. If 3 well-differentiated streams cover the regimes well, that's the right call. Allocation (lot_size_usd, slot_count) is decided at model assembly, not stream tuning.

**`backtest.stream_tests` — 12 rows.** MR v1 (test_ids 1–5), DH v1 (test_ids 6–12).

---

## What's next

### 1. Design stream 3 — think about regime gaps first

With MR and DH locked, the covered regimes are:
- **Bull momentum** (MR): F&G > 25, above 200 SMA, RSI > 55
- **Bear/fear bounce** (DH): F&G < 20, 25%+ drawdown from 90d high, RSI recovering

Gaps to consider for stream 3:
- **Neutral / transitional regime**: neither bull nor extreme fear — sideways/consolidation with no clear directional bias
- **Breakout from range**: consolidation breaks (Breakout Scout spec exists in `docs/specs/streams/`)
- **Trend continuation pull-back**: price in uptrend, pulls back to SMA then bounces (different from MR which is crossover-based)

Read the existing stream specs in `docs/specs/streams/` before designing — the groundwork was laid earlier.

### 2. Model assembly (after all streams locked)
- Set `lot_size_usd` and `slot_count` per stream in `backtest.streams`
- Total must sum to $100
- Run model-level backtest across all streams simultaneously (`backtest.model_tests`)
- Only after model-level test passes → deployment-ready

### 3. Reporting dashboard
Second Streamlit page comparing streams side-by-side and model-level performance.

---

## Key files

| File | Purpose |
|---|---|
| `src/backtester/engine.py` | Backtest engine — `_warmup_days()` + `run_backtest()` |
| `src/backtester/signals.py` | All core signals including `rsi_recovery` with `require_bullish_candle` |
| `src/backtester/indicators.py` | `drawdown_from_high`, RSI, EMA, SMA, ATR |
| `src/backtester/runner.py` | `run()` — call from Claude Code to push results to Stream Tester |
| `src/app/stream_tester.py` | Main Streamlit app (slim orchestrator) |
| `src/app/utils.py` | Helpers: params_hash, label_window, grade_info, descriptions |
| `src/app/db.py` | DB ops: load/save stream tests, pending run management |
| `src/app/dashboard.py` | render_dashboard — all charts and metrics |
| `docs/architecture/stream-attributes.md` | Full attribute reference — keep updated as new signals/filters added |
| `docs/specs/streams/` | Stream specs written pre-build |
