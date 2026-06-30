# Handoff — 2026-06-30

## Done

### v2-rebuild branch — full architecture overhaul committed

Complete from-scratch rebuild on the `v2-rebuild` branch:
- **New schema:** `timeframe_presets` table replaces free-text `window_name`. Preset-based timeframes with FK reference. 4 standard presets seeded.
- **Slot redesign:** `slot_mode` per stream (`single` / `scale_down` / `scale_up`). Slot 2 entry is now intentional and stream-specific, not redundant. Independent trailing stops per lot.
- **`reset_backtest.sql`:** Safely resets all backtest data. `live.*` tables are explicitly excluded with a comment — protected by design.
- All app modules rebuilt: `db.py`, `dashboard.py`, `stream_tester.py`, `model_dashboard.py`, `pages/model_tester.py`.

### Model 1 — clean v2 baseline established

All 3 streams re-locked and all 12 stream tests saved (3 streams × 4 presets).
Model-level tests run and saved for all 4 presets.

**Stream test results (v2 baseline, 1 slot × $10):**

| Stream | Primary | Full History | Recent | 2026 YTD |
|---|---|---|---|---|
| Momentum Rider v1 | +11.4% / 100 trades | +2.9% / 154 trades | +1.0% / 41 trades | -28.1% / 4 trades |
| Dip Hunter v1 | +13.8% / 39 trades | +7.0% / 80 trades | +9.2% / 17 trades | -19.1% / 11 trades |
| Breakout Scout v1 | +13.0% / 18 trades | +6.4% / 30 trades | +0.4% / 8 trades | — / 0 trades |

**Model-level results (equal allocation $33.33/lot × 1 slot × 3 streams = $99.99):**

| Window | Ann. Return | Trades | Max DD |
|---|---|---|---|
| Primary (2019–2023) | **+12.7%** | 157 | -21.2% |
| Full History (2018–) | +5.6% | 264 | -23.4% |
| Recent (2024–) | +3.6% | 66 | -19.6% |
| 2026 YTD | -16.2% | 15 | -8.6% |

**Note on v2 vs v1 numbers:** MR v1 Primary was +17.8% in v1, now +11.4%. Not a param change — v2 engine has slightly different candle boundary handling (end date now inclusive via `timestamp <= '2023-12-31 23:59:59'` vs v1's exclusive `timestamp < '2023-12-31'`). These are the correct v2 baselines. Do not compare to v1 numbers.

**DH params correction:** The spec file showed an early iteration (`rsi_dip`). The actual locked config is `rsi_recovery` with F&G < 20 and 90-day drawdown filter. This is confirmed from the lock commit (`e499c72`).

---

## Key DB state

- `backtest.models` — model_id=1 (Model 1)
- `backtest.streams` — stream_id=1 (MR), 2 (DH), 3 (BS), all locked with slot_count=1 / slot_mode=single
- `backtest.stream_tests` — test_ids 1–12 (3 streams × 4 presets), all with pkl files in `src/app/runs/`
- `backtest.model_tests` — model_test_ids 1–4 (4 presets, run #1), all with pkl files in `src/app/model_runs/`

---

## What's Next

### 1. Experiment with slot behavior per stream (ready to start)
Now that clean baseline data is in and the engine supports `slot_mode`, test each stream with its natural slot behavior:
- **DH:** `slot_mode='scale_down'` — slot 2 averages down when price drops X% from slot 1 entry
- **MR:** `slot_mode='scale_up'` — slot 2 pyramids up when trade is winning and signal re-fires
- **BS:** Keep `slot_count=1` — confirmed: a failed breakout is a bad place to double down

### 2. Consider adjusting MR trailing stop
MR got +1.0% in the 2024 bull year. The 5% trailing stop is good for trend-riding but got shaken out in the aggressive 2024 bounce. Could test 7-8% trail on MR in isolation before the next model assembly.

### 3. Streams 4 and 5 (optional for Model 2 design)
Current 3-stream model may be complete enough for Model 1. Decision point when user returns.

---

## Key files

| File | Purpose |
|---|---|
| `src/backtester/engine.py` | Slot logic: `_run_slot()`, `_derive_slot2_signals()`, `slot_mode` dispatch |
| `src/backtester/model_runner.py` | Entry point — pass explicit allocations |
| `src/data/reseed_model1.py` | Reseed script — run to restore DB from scratch |
| `src/data/reset_backtest.sql` | Wipe backtest schema (excludes `live.*`) |
| `src/app/db.py` | All DB ops including `save_stream_test`, `save_model_test` |
| `src/app/dashboard.py` | Stream Tester charts and save UI |
| `src/app/model_dashboard.py` | Model Tester charts including racing lines |
