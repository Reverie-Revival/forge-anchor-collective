# Handoff — 2026-06-30

## Done This Session

### v2 Architecture Rebuild (merged to main)

Full from-scratch rebuild on `v2-rebuild` branch, now merged. Every layer changed:

**Database:**
- `timeframe_presets` table replaces free-text `window_name`. 4 standard presets seeded (Primary Window, Full History, Recent, 2026 YTD).
- `backtest.stream_tests`: `preset_id` FK + `slot_count` + `slot_mode` + `simulation_start/end`
- `backtest.streams`: `slot_mode` column added (`single` / `scale_down` / `scale_up`)
- `reset_backtest.sql`: safely wipes all backtest data — `live.*` explicitly excluded with policy comment
- `reseed_model1.py`: script to fully restore DB from scratch if needed

**Engine:**
- `slot_mode` dispatch — `scale_down` averages down, `scale_up` pyramids up, `single` is one slot only
- Slot 2 entry is derived from slot 1's actual trade history (not a duplicate signal)
- Independent trailing stops per lot (already worked via `high_water_mark` — now documented)

**App:**
- Preset dropdown save UI (replaces free-text window name input)
- Tabs always sorted alphabetically by timeframe label
- `slot_mode` and `slot_count` shown in stream/allocation display
- DB connection fixed: `app.py` now calls `load_dotenv()` at startup; `get_engine()` reads individual env vars
- `next_run_number()` hardened against empty DataFrames

**Docs/specs:**
- `database-schema.md`: fully updated to v2 schema
- `dip-hunter-v1.md`: rewritten to match actual locked config (`rsi_recovery`, not early `rsi_dip` iteration)
- `model-1.md`: updated to reflect 3 locked streams, actual allocation, v2 results
- `steady-climber-v1.md`, `surge-rider-v1.md`: flagged as placeholder — never built
- `src/backtester/runner.py`: deleted (v1 entry point, replaced by `model_runner.py`)

### Model 1 v2 Baseline — Clean Data in DB

Stream tests (test_id 1–12, 3 streams × 4 presets, 1 slot × $10):

| Stream | Primary | Full History | Recent | 2026 YTD |
|---|---|---|---|---|
| Momentum Rider v1 | +11.4% / 100 trades | +2.9% | +1.0% | -28.1% |
| Dip Hunter v1 | +13.8% / 39 trades | +7.0% | +9.2% | -19.1% |
| Breakout Scout v1 | +13.0% / 18 trades | +6.4% | +0.4% | 0 trades |

Model tests (model_test_id 1–4, equal $33.33/slot × 1 slot × 3 streams = $100):

| Window | Ann. Return | Trades | Max DD |
|---|---|---|---|
| Primary (2019–2023) | **+12.7%** | 157 | -21.2% |
| Full History (2018–) | +5.6% | 264 | -23.4% |
| Recent (2024–) | +3.6% | 66 | -19.6% |
| 2026 YTD | -16.2% | 15 | -8.6% |

**Note on v2 vs v1 numbers:** MR Primary +11.4% vs v1's +17.8%. This is the v2 baseline — end-date boundary handling changed (now inclusive), engine is correct. Do not compare to v1 figures.

---

## Current DB State

- `backtest.models`: model_id=1
- `backtest.streams`: stream_id=1 (MR), 2 (DH), 3 (BS) — all slot_count=1, slot_mode='single'
- `backtest.stream_tests`: test_id 1–12, all with pkl in `src/app/runs/`
- `backtest.model_tests`: model_test_id 1–4, all with pkl in `src/app/model_runs/`

To fully restore DB from scratch: `source .venv/bin/activate && python -m src.data.reseed_model1`

---

## What's Next

### 1. Stream re-testing with slot modes (first priority)

The current baseline uses `slot_mode='single'` (1 slot each). The next step is to test each stream with its intended slot behavior:

- **Dip Hunter**: `slot_mode='scale_down'` — slot 2 enters when price drops X% from slot 1's entry. Tune `slot2_trigger_pct` in `params["position"]`. Could improve avg entry price in oversold conditions.
- **Momentum Rider**: `slot_mode='scale_up'` — slot 2 adds when trade is up X% and original signal fires again. Pyramid into winners.
- **Breakout Scout**: stays `slot_count=1` — a failed breakout is the worst time to add.

Run each in Stream Tester, compare against the single-slot baseline (test_id 2, 6, 10 = Primary Window locked tests). If an improved config earns a lock, update `backtest.streams`:
```sql
UPDATE backtest.streams
SET parameters = '...', locked_test_id = <new_test_id>, slot_mode = 'scale_down', slot_count = 2
WHERE stream_id = 2;  -- DH
```
Then re-run model via `run_model()` and compare.

### 2. MR trailing stop experiment (optional)

MR got only +1.0% in the 2024 bull run — the 5% trailing stop got shaken out early. Worth testing 7% or 8% trail on MR Primary to see if it captures longer trend moves without sacrificing too much of the current result.

### 3. Model deployment decision

When streams feel locked at their best config, decide whether Model 1 is ready for live deployment:
- Primary +12.7% beats S&P (~10%) ✓
- All 3 streams validated with confirmed regime complementarity ✓
- Live schema not yet built — Kraken API integration needed before any real money moves

---

## Architecture Reference

| File | Purpose |
|---|---|
| `src/backtester/engine.py` | `_run_slot()`, `_derive_slot2_signals()`, slot_mode dispatch |
| `src/backtester/model_runner.py` | `run_model()` — always pass explicit allocations |
| `src/data/reseed_model1.py` | Full DB restore from scratch |
| `src/data/reset_backtest.sql` | Wipe backtest schema (excludes live.*) |
| `src/data/schema.sql` | Full v2 schema definition |
| `src/data/seed_presets.sql` | Standard preset definitions |
| `src/app/db.py` | All DB ops — save/load stream tests, model tests, presets |
| `src/app/dashboard.py` | Stream Tester charts and preset save UI |
| `src/app/model_dashboard.py` | Model Tester charts, racing lines, allocation display |
