# Handoff — 2026-06-30

## Done

### Model Tester bug fixes
- **run_number grouping bug** — model_tests 5–8 (MR v2 assembly) were all saved as `run_number=1`, collapsing them into the same sidebar entry as the stale MR v1 results. Root cause: `next_model_run_number()` relied on a passed `history` DataFrame that was empty when those tests were saved.
  - Fixed: `next_model_run_number` now queries the DB directly — no history parameter, can't go stale again
  - DB patched: ids 5–8 updated to `run_number=2`
  - Sidebar run label now shows stream config inline (e.g. `#2  MR v2 · DH v1 · BS v1  ·  +16.0%  ·  4 windows`)
  - Stream alloc summary now shows full stream name including version (was truncating to first word)
- **Wrong 4th preset** — Run #2 had Primary v1 (2019–2023) as the 4th window instead of 2026 YTD. Deleted model_test_id=8, re-ran on 2026 YTD (preset_id=4), saved as model_test_id=10.
- **MR v2 color** — added `"Momentum Rider v2": "#22c55e"` to `STREAM_COLORS` in model_dashboard.py

### Stream Tester bug fixes
- **KNOWN_STREAMS** — updated from "Momentum Rider v1" to include "Momentum Rider v2", sorted alphabetically: BS v1, DH v1, MR v1, MR v2, SC v1, SR v1
- **Stale stream_name in pkls** — `.last_run.pkl` and run pkls 24–28 all had `stream_name = "Momentum Rider v1"` despite being v2 runs. Patched all 6 in place.
- **run_stream_test.py defaults** — updated `STREAM_NAME` to "Momentum Rider v2" and `PRESET_NAME` to "Primary v2"

---

## Current DB State

**backtest.streams:**
- stream_id=1: Momentum Rider v2 — LOCKED (locked_test_id=26)
- stream_id=2: Dip Hunter v1 — locked, undertested on Primary v2
- stream_id=3: Breakout Scout v1 — locked, undertested on Primary v2

**backtest.model_tests:**
| model_test_id | run_number | preset | ann% |
|---|---|---|---|
| 1 | 1 (MR v1) | Full History | +5.6% |
| 2 | 1 (MR v1) | Primary Window (v1) | +12.7% |
| 3 | 1 (MR v1) | Recent | +3.6% |
| 4 | 1 (MR v1) | 2026 YTD | -16.2% |
| 5 | 2 (MR v2) | Primary v2 | +8.5% |
| 6 | 2 (MR v2) | Full History | +16.0% |
| 7 | 2 (MR v2) | Recent | +9.1% |
| 10 | 2 (MR v2) | 2026 YTD | -6.6% |

**What the numbers tell us:**
MR v2 alone hits +21.5% on Primary v2. Combined model only gets +8.5% — DH (11 trades, -19.1% annualized on 2026 YTD) is dragging badly. BS fired zero times on 2026 YTD.

---

## Next Up

### Priority: Tune Dip Hunter for Primary v2

DH v1 was locked against Primary v1 (2019–2023) and never stress-tested on the 2022-present regime. The model assembly result exposes this.

**Start here:**
1. Baseline run — DH v1 on Primary v2 (preset_id=5):
```python
# In run_stream_test.py, set:
STREAM_NAME = "Dip Hunter v1"
PRESET_NAME = "Primary v2"
# Use DH's locked config from DB (stream_id=2)
```

Or pull the locked config directly:
```python
from src.app.db import get_engine
from sqlalchemy import text
with get_engine().connect() as conn:
    print(conn.execute(text("SELECT parameters FROM backtest.streams WHERE stream_id=2")).fetchone())
```

2. Check trade count on PV2. DH fires on fear bounces (F&G < 20, RSI recovery through 30, price 25%+ below 90d high). The 2022-2023 bear had these; 2024-2025 bull had fewer.
3. If trade count is very low: relax F&G threshold or % below high requirement
4. If trades are there but returns are poor: widen/tighten trailing stop, adjust RSI entry threshold
5. Run any improvements across Full History + Recent to verify generalization
6. Save to DB via `save_stream_test()`, review in Stream Tester

**Batch exploration template:** copy `src/backtester/explore_mr_v2b.py` — structure is identical for DH

**Target:** DH and BS strong enough on PV2 that combined model clears 15%+ on that window.

---

## Architecture Reference

| File | Purpose |
|---|---|
| `src/backtester/engine.py` | Core backtest engine — `_run_slot()`, slot_mode dispatch, `_warmup_days()` |
| `src/backtester/model_runner.py` | `run_model()` — loads locked streams from DB, applies allocations |
| `src/backtester/model_engine.py` | Low-level multi-stream runner called by model_runner |
| `src/backtester/run_stream_test.py` | Quick single-config runner → writes .last_run.pkl for Stream Tester |
| `src/backtester/explore_mr_v2b.py` | Template for batch exploration — copy and adapt for DH/BS |
| `src/app/db.py` | All DB ops — `save_stream_test()`, `save_model_test()`, `get_engine()` |
| `src/app/dashboard.py` | Stream Tester renderer |
| `src/app/pages/model_tester.py` | Model Tester page |
| `src/app/model_dashboard.py` | Model dashboard renderer |

**Run Streamlit:** `streamlit run src/app/app.py`
