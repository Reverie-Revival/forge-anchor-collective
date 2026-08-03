# Handoff — 2026-08-02

---
## ⚠️ ACTION REQUIRED — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Log into your credit card and confirm zero Oracle charges ever appeared.** The account should be fully deleted by now — verify it's gone and no recurring relationship exists. This has been reminded every session since the deletion was submitted — please confirm and this banner comes down.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, cron on schedule. Full alert coverage active (order placed, filled, closed, expired, system down).

**Model 2 is assembled and backtested.** Run 3 selected as deployment config. Not yet deployed — deprioritized behind Model 3.

**Model 3 is LIVE — trading for real, as of 2026-08-02 ~00:35 UTC.** "Grid Stacker Blended", $100 real capital in Kraken. `DRY_RUN_M3=false`. Real Kraken connectivity confirmed (`Kraken connected — USD: $166.54 BTC: 0.00053149` — whole-account balance, shared with Model 1; Model 3 sizes off its own `live.blended_capital` ledger, never this figure). Cron-job.org's two Model 3 jobs (`executor_m3.yml` every 30min, `healthcheck_m3.yml` every 2h) both confirmed firing successfully, dispatching with `ref:live-model-3` (switched from `ref:main` after the incident below made clear why that matters). `live-model-3` branch is fast-forwarded to match `main` exactly as of commit `3a11cf6`.

**Explicit user decision, not the original plan:** the original plan called for a multi-day dry-run logging trial before going live. User explicitly chose to skip it ("I don't mind a little turbulence... would rather have a good order happen for real than miss out because it's a dry-run") — flagged clearly before flipping the switch that real order placement had zero live Kraken mileage (only ever tested against a mock), user accepted that knowingly. **As of this handoff, no signal has fired yet and no real order has been placed** — first real trade, whenever it happens, has genuinely never been observed end-to-end in production. Watch the first one closely.

**Model Dashboard is BUILT** — `3_model_dashboard.py` live in the multipage app (port 8504).

**⚠️ INCIDENT — 2026-08-02, ~14:30 UTC to ~00:10 UTC (~9.5h):** Model 1's executor, healthcheck, and market_data cron jobs all went silent simultaneously. Two independent, compounding causes:

1. **cron-job.org's stored GitHub PAT expired** (401 Unauthorized on every dispatch) — the loud, immediately-visible symptom, blocking all three jobs from even reaching GitHub Actions. Fixed: generated a new classic PAT with **no expiration** (removes the recurring-rotation problem going forward) and updated it in cron-job.org for all 5 jobs (Model 1's 3 + Model 3's 2).
2. **A hidden env-var mismatch on `main`**, only exposed once #1 was fixed and dispatches could actually reach a job: commit `5893c06` (pre-existing, not from this session) renamed the env var `executor.yml`/`market_data.yml`/`healthcheck.yml` pass from `DATABASE_URL` to `SUPABASE_DATABASE_URL` on **`main` only**, deliberately leaving `live-model-1`'s copies on the old name — assuming cron-job.org always dispatched these against `live-model-1`. That assumption was correct for `healthcheck.yml` but **wrong for `executor.yml`/`market_data.yml`**, which have always dispatched with `ref:main` (confirmed via GitHub Actions run history). Once `5893c06` landed on `main`, any `ref:main` dispatch passed the wrong env var name to code (still checked out from `live-model-1` regardless of dispatch ref) that only reads `DATABASE_URL` — silent fallback to a nonexistent local Postgres, `connection refused`.
   - Immediate fix: switched cron-job.org's executor + market_data jobs to `ref:live-model-1` (matching what was already working for healthcheck).
   - Durable fix (commit `d215914`): reverted `main`'s copies of all three workflow files back to `DATABASE_URL`, verified byte-identical to `live-model-1`'s copies. Now it no longer matters which ref cron-job.org dispatches with for these three jobs — eliminates this whole class of bug.

**Blast radius assessed and found clean:** during the outage, Model 1's one open lot (Breakout Scout, entry $62,710.10, high_water_mark $66,808.20, 10% trail → stop level $60,127.38) was unmonitored, but BTC's low across the entire outage window never dropped below ~$62,993 — the stop was never actually breached, confirmed by backfilling the missed candles and checking the min/max. No missed stop, no incorrect fill, no data loss. Kraken connectivity itself was never affected (confirmed $166.54 USD / 0.00053149 BTC balance readable throughout).

**Note on SMS alerts:** user reported only receiving email, not SMS, for the system-down alert during this incident. Root cause not yet confirmed — possibly the documented T-Mobile burst-rate-limiting (see "Alert Coverage" below) or a misconfigured `ALERT_TO_SMS` gateway address. Needs a follow-up check next session (can't diagnose further without reading the actual secret value).

**A related bug surfaced when Model 3's executor was first triggered for real** (commit `3a11cf6`): `signal_engine.check()` internally calls `engine.py`'s `load_market_data()`, which reads `DATABASE_URL` specifically (not `SUPABASE_DATABASE_URL`, with a localhost fallback) — same root cause as the incident above, different file. `executor_m3.yml` only set `SUPABASE_DATABASE_URL`; fixed by also mapping the same secret to `DATABASE_URL`. Local testing hadn't caught this because local `.env` has `DATABASE_URL` pointing at a Postgres that also happens to have synced market data, masking the CI-only gap. Fixed and verified by simulating the CI env exactly before pushing.

**Also fixed proactively:** switched Model 3's two cron-job.org jobs from `ref:main` to `ref:live-model-3` (matching checkout) before this class of bug could ever bite Model 3 the way it bit Model 1 — `live-model-3` was fast-forwarded to `main`'s tip first so the switch didn't regress anything.

---

## Done This Session (2026-08-02) — Model 3 live execution built, isolated, and tested

Branched `feature/model3-live-build` off `main` for the whole session. Committed last session's uncommitted Grid Stacker Blended backtester work first (it had never been committed — engine.py, dashboard.py, metrics.py changes from 2026-08-01).

### Isolation design (the actual hard requirement this session)

Same Kraken account as Model 1, same Supabase project (same `SUPABASE_DATABASE_URL` — no new Supabase project, stays $0). Model 1 must keep running unaffected; Model 3 must never be able to spend Model 1's money even though compounding means Model 3's own capital isn't a fixed $100.

- **New tables, not a retrofit of `live.lots`**: `live.blended_positions` (one row per stack: status, avg_cost_basis, total_qty, capitulation_armed, pending-entry/pending-add tracking) + `live.blended_fills` (child rows per fill) + `live.blended_capital` (Model 3's own tracked capital ledger, seeded at $100, updated only on realized close).
- **Capital ledger, never Kraken's account balance**: every position sizes off `live.blended_capital.available_capital`, frozen per-position as `position_capital_base` at open time (mirrors the backtester's frozen `slot_capitals` split). Model 3 can only ever spend money it has itself realized — it has no code path that reads or reasons about the shared account's actual USD/BTC balance.
- **`live.executor_state` was a shared singleton (`id=1`)** — added a nullable `model_id` column, backfilled Model 1's row to `model_id=1`, unique-indexed on `model_id`. Model 3's executor reads/writes its own row keyed by `model_id`, never touches Model 1's.
- **Separate process, separate everything**: `src/live/blended_executor.py` (own tick loop), `blended_order_manager.py`, `blended_position_monitor.py`, `blended_notifier.py`, `blended_healthcheck.py`, `.github/workflows/executor_m3.yml` + `healthcheck_m3.yml` (separate `DRY_RUN_M3` secret from Model 1's `DRY_RUN`). `executor.py` (Model 1, live, production) was never modified — only read from (two pure helper functions imported: `_detect_closed_timeframes`, `_latest_candle_for_stream`).

### Model 3 formally finalized in the backtest schema (a gap this session found)

CLAUDE.md's model lifecycle requires assembling into `backtest.model_streams` and running a model-level backtest before a model is "deployment-ready" — Model 3 had only ever been validated at the stream level (see prior session below). Found and fixed a real bug in `model_engine.py` along the way: it computed `initial_capital = lot_size_usd * slot_count` for every slot_mode, but blended mode's `lot_size_usd` already **is** the total capital pool (split internally via `slot_capital_weight`), not a per-slot amount — this would have 5x-inflated Model 3's capital if run through the model-level tooling before the fix. Fixed, then ran `src/backtester/finalize_model3.py` (new, one-time): created `backtest.models` + `backtest.model_streams` rows, ran the model-level backtest (Full History), got **+84.7% ann, 482 trades** — matches the stream-level number from 2026-08-01 almost exactly, confirming the finalization path is correct. Saved as `backtest.model_tests.model_test_id=106`.

### Real bugs caught by testing (this is why the test suite matters)

1. **`executor_state` id collision** — the fallback INSERT in `_write_last_run` didn't specify `id`, so it silently tried the column's `DEFAULT 1` and collided with Model 1's row. Fixed to key off `model_id`.
2. **Breakeven floor used the wrong fee.** `blended_position_monitor.py`'s "never voluntarily realize a loss" floor divided by `(1 - MAKER_FEE)`, but the real exit (`place_exit`) is a market sell charged `TAKER_FEE` (0.40% vs 0.25%) — same known fee-type gap already documented for Model 1's live exits, just not yet accounted for here. The mismatch let a test position close at a small real loss (-$0.06 on a $40 position) instead of flat/positive. Fixed to `avg_ep / (1 - TAKER_FEE)`, exactly matching `place_exit`'s real pnl formula. Cleaned up the unused `MAKER_FEE` import and added a fee-model docstring to `blended_order_manager.py` so this can't silently drift back.
3. **`deploy_model3.py` queried `backtest.stream_configs` over `SUPABASE_DATABASE_URL`** — Supabase has no `backtest` schema at all (confirmed: live schema + market_data + sentiment_data only). Split into two connections: local Postgres to read the locked stream config, Supabase to write `live.models`/`live.streams`/`live.blended_capital`.
4. **SQLAlchemy `text()` doesn't safely parse `:name::type`** (a cast immediately after a bind param) — `deploy.py`'s original pattern silently fails a syntax check in the installed SQLAlchemy version. Used `CAST(:params AS jsonb)` instead in `deploy_model3.py`. Not fixed in `deploy.py` itself (already run once for Model 1, guarded against re-running, out of scope to touch production deploy code for a latent bug that can't fire again).
5. **Tests were sending real email/SMS alerts.** Testing the real (non-dry-run) fill/exit code paths necessarily calls the real notifier. Added `tests/live/conftest.py` — an autouse fixture that blanks the `ALERT_*` env vars for every test in `tests/live/`, so `notifier._dispatch`'s own "not configured" guard skips silently. Also cut total test suite runtime from ~9 minutes to ~2 seconds (the real SMTP round-trips were the entire cost).

### Test suite — 16/16 passing (`tests/live/`)

- `test_blended_math.py` (8 tests, no DB) — slot-weight splitting (equal, uneven, compounding-scaled), cascade trigger price math, capitulation price math, minimum lot-size threshold, and an explicit regression guard pinning `TAKER_FEE > MAKER_FEE` in the breakeven formula.
- `test_blended_state_machine.py` (8 tests, local Postgres sandbox, mocked Kraken) — full entry→add→trailing-stop-exit cycle, entry-order expiry frees the slot, cascade-add expiry keeps the position open for retry (doesn't close it), capitulation stop realizing an allowed loss once all 5 slots are filled, compounding correctly growing the next position's capital base, duplicate-entry blocking, minimum-lot-size skip placing zero orders.
- `test_blended_isolation.py` (1 test) — seeds a fake Model 1 lot + heartbeat row, runs a full Model 3 cycle through the real (non-mocked-DB) code paths, asserts Model 1's lot row is byte-for-byte unchanged and its heartbeat timestamp untouched.
- Dry-run mode was confirmed to skip all Kraken polling (same as Model 1's `order_manager`) — meaning dry-run alone would never have exercised the fill/add/exit logic. The state-machine and isolation tests deliberately use `dry_run=False` with a `FakeKraken` stub instead, which is what actually caught bugs #1 and #2 above.
- **Pre-existing, unrelated**: `tests/live/test_signal_parity.py` (Model 1's regression test) is currently broken — it queries `backtest.streams.locked_test_id`/`.parameters`/`.model_id`, columns that don't exist in the current v3 schema (`backtest.streams` is identity-only now; config lives in `backtest.stream_configs`). Not touched or caused by this session — flagging for a future cleanup pass.

### What did NOT change

Model 1 (live) and Model 2 (backtested, not deployed) — completely untouched in behavior. `executor.py`, `order_manager.py`, `position_monitor.py`, `notifier.py`, `live.lots`, `live.models`, `live.streams` structure all unmodified. The only shared-table changes were additive/nullable (`executor_runs.model_id`, `executor_state.model_id`) and were applied to both local Postgres and Supabase, verified against Model 1's real row afterward.

### Second QA pass (same session, prompted by "did you test this hard enough?")

Fair challenge — the first pass tested individual functions well but never exercised the actual production entry point (`tick()`) end-to-end, and a real gap slipped through:

**Found and fixed: partial-fill-before-expiry could silently discard real BTC.** If a limit order partially filled right before its expiry timestamp, the old code cancelled the order and unconditionally `DELETE`d the position row — losing track of BTC that was actually bought (cancelling a partial fill only stops further fills, it doesn't undo what already executed). This exact same gap exists in Model 1's `order_manager.py` too (mirrored from there), but wasn't fixed here since Model 1 is already live — flagging for a future dedicated pass, not touching production code today. Fixed for Model 3: `check_pending_entry`/`check_pending_add` now always query final order status (even after cancelling) and only free the slot/clear the pending state when `vol_exec` is confirmed zero; any nonzero volume is folded into the position via new `_apply_entry_fill`/`_apply_add_fill` helpers, whether it's a full or partial fill.

**Added `tests/live/test_blended_executor_tick.py`** — calls `blended_executor.tick()` itself (the actual function GitHub Actions invokes every 30 min), not just the lower-level functions it calls. Covers: entry placed on a firing signal, no double-entry when the same signal keeps firing across repeated ticks, and survives a Kraken API exception mid-poll without losing position state.

**Upgraded the test `FakeKraken` stub** to actually simulate unfilled ("none") and partially-filled ("partial") orders, not just instant-full-fill — the original stub made the "expiry" tests pass even under the old buggy delete-unconditionally code, because it never modeled a genuinely unfilled order in the first place. Caught a real test-suite bug in the process too: `test_blended_executor_tick.py` had accidentally grown its own separate, un-upgraded copy of `FakeKraken` that silently ignored `next_fill_mode` — deduplicated all three test files onto one shared `tests/live/_fake_kraken.py`.

**A second, subtler version of the same bug class turned up while writing the test for the first one:** the initial fix treated ANY `vol_exec > 0` as final. But a genuine partial fill on an order still resting `"open"` on Kraken's book (not yet cancelled or fully filled) could keep filling further — finalizing it early would stop polling that order and silently orphan whatever fills later. Fixed: fills are only finalized on a *terminal* status (`"closed"` = fully filled, or `"canceled"`/`"expired"` with `vol_exec > 0` = partial-then-cancelled). A still-`"open"` order, partial or not, is left untouched and polled again next tick. If our own expiry already passed but the order still reports `"open"` (cancel didn't take effect — race or API hiccup), nothing is touched either; it logs a warning and retries the cancel next tick rather than risk deleting a live order's tracking.

**Test suite now 22/22** (`pytest tests/live/ --ignore=tests/live/test_signal_parity.py`): 8 pure math, 13 state-machine integration (9 original + 3 new partial-fill scenarios — at-expiry for both entry and add, plus still-resting-not-yet-finalized), 1 isolation, 3 tick-orchestration.

**Still not covered, worth knowing about before flipping to real orders:** signal-detection parity for Model 3's exact param set against real historical dip events (the shared `signal_engine.check()` function is untouched and already relied on by live Model 1, but no test explicitly re-validates it for Model 3's `fear_dip`/4h/dip_pct=1.0 config); concurrent-tick / race-condition behavior if Model 1 and Model 3's workflows ever overlap in execution (each only touches its own model_id-scoped rows, so should be safe by construction, but untested under real concurrency).

### Cleanup pass + real Supabase deploy (same session)

Ran a 4-parallel-agent `/simplify` review (reuse, simplification, efficiency, altitude) over the full day's diff. Applied: extracted `slot_capitals_for()` into a new shared `src/backtester/slot_math.py` so live and backtest math can no longer silently drift (verified behavior-identical — re-ran the Grid Stacker backtest post-refactor, got the exact same 482 trades / 84.71% ann / $19,275.25 pnl); extracted a shared `_resolve_order()` helper for the cancel/requery/classify polling logic that's already had two real bugs fixed in it today; removed a redundant `live.streams` query on every fill by threading the already-loaded `streams` dict through; a few smaller dedups (`_tf_minutes`, a notifier label helper, three test files' identical `_get_engine()`). Skipped anything requiring changes to already-live `executor.py` or files outside today's diff. 22/22 tests still passing after.

**Then deployed Model 3's DB rows to Supabase for real** (`python -m src.live.deploy_model3`) — `live.models.model_id=3`, `live.streams.stream_id=4`, `live.blended_capital` seeded at $100.00, all confirmed via direct query. Model 1's `live.lots` confirmed unchanged (still 1 row) immediately after. **This is DB bookkeeping only — no Kraken order has been placed, no branch has been cut, and DRY_RUN_M3 doesn't exist as a secret yet.** See "What's Next" above for the remaining steps before any real trading starts.

---

## Done Prior Session (2026-08-01) — Model 3 candidate discovered, tuned, and QA'd

Ran data sync. Picked up the low-volatility uptrend question from last session (closed out — see prior entry below), then built something that turned into the main event of this session: a new stream design that ended up being the best-performing thing in this project by a wide margin.

### The design: "Grid Stacker Blended" (`stream_id=11`)

Originated from wanting a DCA/cascade-style stream (buy dips, average down, never sell at a loss) but one that trades *frequently*, unlike the earlier attempts. Went through several structural iterations before landing on the final design:

- **Rejected: independent-slot cascade with FIFO rotation** — would have required realizing real losses to keep cycling capital deeper in a crash; math showed this could spiral (each rotation ~41% loss on that slot) — abandoned before building.
- **Landed on: blended-average design** — ONE position built from up to 5 fills, tracked via a true weighted-average cost basis (in BTC-quantity terms, not naive price average), with ONE combined exit for the whole stack instead of per-slot exits. New `slot_mode='blended'` added to `src/backtester/engine.py` (`_run_blended_slots()`), purely additive — doesn't touch any existing slot mode or any other stream.

### Real bugs found and fixed during verification (not just "looks fine" — actually traced against real data)

1. **Fee double-counting in the breakeven floor.** The "never sell at a loss" floor was computed as `avg_ep * (1 + fee*2)`, but `avg_ep` already had the buy-side fee baked in (from quantity reduction at fill time), so this double-counted it — breakeven exits were landing at +0.25% instead of true $0. Fixed: `avg_ep / (1 - fee)`. Verified down to floating-point noise after the fix.
2. **Cascade adds filled instantly at zero simulated latency**, unlike slot 1's realistic pending-limit-with-expiry simulation. Fixed by routing adds through the same 2-candle limit-order-with-expiry mechanism. Re-verified: barely moved the numbers (24.26% vs 24.31% ann on Full History) — confirms the edge didn't depend on the unrealistic assumption.

### Features added to the engine (`_run_blended_slots`, all opt-in via `position` params, zero effect on any other stream)

- **`compound`** (bool) — position sizing grows/shrinks with account balance between positions instead of staying fixed. User's explicit, final decision: **compounding IS wanted for Model 3** — do not exclude it.
- **`capitulation_stop_pct`** — a hard backstop that only arms once ALL 5 slots are filled (out of ammo). If price falls further below the last fill's price, closes the WHOLE position instead of holding indefinitely into an untested crash. Set to 15% (empirically, real triggers start around 8-10%, so 15% is a real margin, not an arbitrary guess). **This actually fired once in real history** — Aug 2024 flash crash, realized -21.3% on $100, closely matching a synthetic walkthrough done earlier (-20.92%). Confirms the backstop works as designed under real conditions, not just in theory.
- Both fill-level detail (`fill_prices`, `fill_timestamps`, `fill_capitals`, `fill_qtys`) added to every trade record so Stream Tester can show a proper hierarchical breakdown (see UI section below).

### Parameter tuning — extensive, all re-validated after compounding was correctly included

Tuned across: entry sensitivity (`dip_pct`), cascade ladder shape (`cumulative_drop_pcts`), exit arm threshold (`trail_arm_gain_pct`), trailing stop width (`trailing_stop_pct`), slot weighting (equal vs front/back-loaded), slot count (3 vs 4 vs 5), and primary timeframe (1h vs 4h). **Every one of these came back confirming the current locked config as the winner** — 1h looked better on the headline number but failed walk-forward and introduced real losses where 4h has none; wider trail_pct produces bigger individual wins but loses under compounding because compounding rewards frequency over per-trade size; non-equal weighting lost to equal in both directions tested.

### QA — the part worth trusting

- **5-way rolling walk-forward** (train on expanding window 2018→2020/21/22/23/24, test the following year each time): every single out-of-sample year was positive (31-129% annualized), and the same winning parameters were independently selected in all 5 splits — a real sign of a stable relationship, not noise-chasing.
- **Corrected bootstrap analysis** (10,000 resamples with replacement, properly modeling partial capital deployment per trade — first attempt was flawed, assumed 100% capital utilization on every trade; fixed and re-verified against the real engine to the penny before trusting it). Actual historical result sits at the **51st percentile** of the distribution — not a lucky outlier. But the distribution itself is wide (5th-95th percentile: $8.3K-$48.2K starting from $100 over 8 years) — future results could reasonably land anywhere in that band even with the exact same true edge.
- Full line-by-line code review of the exit-priority logic (capitulation vs. trailing stop interaction) — no bugs found.

### Final locked config: `stream_config_id=36` (v8), also `v7`=35 (identical params, v8 has fuller documentation)

```
primary_timeframe: 4h | core_signal: fear_dip (any 1%+ single-candle drop, dip_pct=1.0)
slots: 5, equal $20 weight each | cumulative_drop_pcts: [1, 2, 5, 10]
trailing_stop_pct: 5.0 | trail_arm_gain_pct: 4 | capitulation_stop_pct: 15 | compound: True
```
Backtest results (compounded, $100 start): Full History 84.77% ann ($100→$19,371) · Primary v2 54.30% · Recent 61.84% · 2026 YTD 24.89% · zero real losing trades anywhere except the one Aug 2024 capitulation event when tested at looser trail settings (not present at the final trail_pct=5 config).

### UI work (`src/app/dashboard.py`)

Built a hierarchical "Blend" trade log view specifically for `slot_mode='blended'` streams — top-level rollup per position (starting/ending capital, blended avg cost, total BTC, fees paid split buy/sell, per-slot dollar amount so compounding growth is visible) with the individual fills nested underneath. Iterated several times based on real usage: fixed a `TypeError` from an older saved payload missing new fields, corrected trade-count language (blends vs. actual buy orders — no double-counting sells), added "STILL OPEN" labeling for the one position still open when data runs out (was being mislabeled as a loss), fixed a reconciliation caption that was double-subtracting the buy fee.

### What did NOT change

Model 1 (live) and Model 2 (backtested, not deployed) — completely untouched. Every addition to the engine this session was purely additive (new slot_mode, new opt-in position params) — verified zero effect on existing streams.

---

## Done Prior Session (2026-07-09)

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

### 🎯 NEXT SESSION — Dashboards need to support Model 3 (and show both models cleanly)

Model 3 is live and trading (see "Current State" above). **Right now there is no way to see it working** — both dashboard pages assume Model 1's `live.lots` shape and don't know Model 3 exists. This is the priority for next session; everything else is secondary.

**Researched this session so next session can start from facts, not rediscovery** (full detail from an Explore-agent survey of `2_live_monitor.py`, `3_model_dashboard.py`, and `db.py`):

#### 1. Live Monitor (`src/app/pages/2_live_monitor.py`) — needs to work for both models

- Currently **completely single-model with no model awareness at all**: `load_open_lots`/`load_pending_lots`/`load_closed_lots` (and the trailing-stop lookup) query `live.lots` with **no `model_id` filter whatsoever** — it shows every lot from every model mixed together with no labeling. Once Model 3 has real lots-shaped... except it won't, because Model 3 doesn't produce `live.lots` rows at all (see below) — so today, Model 3 activity is just invisible here, not mixed in incorrectly.
- `load_model_info()` assumes exactly one row in `live.models` has `status='active'` — that assumption is already false (Model 1 AND Model 3 are both `status='active'` right now), so whichever one the query happens to return first is what gets shown as "the" model.
- **Needs**: some notion of "which model(s) am I looking at" — a toggle/tabs (Model 1 / Model 3 / both) is probably right given the user's ask to "still be able to see both production working branch models" easily. A combined view is harder to get right (different position shapes) — tabs or a selector is the more tractable v1.

#### 2. Model Dashboard (`src/app/pages/3_model_dashboard.py`) — needs blended/compounding support, mirroring what Stream Tester already solved

- Already has a **live/backtest source toggle and a model selector** — good bones. But: the model selector (`load_models()` in `db.py`) reads from `backtest.models` (local Postgres) — **Model 3 needs a `backtest.models` row for it to even appear in the sidebar** (it has one — `finalize_model3.py` created `backtest.models` model_id for it locally — but worth double-checking the selector's query actually surfaces it correctly given it's keyed differently than live's `model_id=3`).
- The Live path calls `db.load_dashboard_lots(model_id, "live", None)`, hardcoded to query `live.lots` (`db.py` line ~663) — wrong shape for Model 3 (`avg_cost_basis`, `total_qty`, `total_deployed`, `capitulation_armed`, fills-as-child-rows, no `slot_number`/`entry_price`/`high_water_mark` single-fill fields).
- **This is the same display problem Stream Tester already solved for BACKTEST blended trades** — `_render_blended_trade_log()` in `src/app/dashboard.py` renders a hierarchical "Blend N" expander (rollup + nested fills) for `slot_mode='blended'` backtest results. That function reads a pandas DataFrame with list-valued columns (`fill_prices`, `fill_qtys`, etc.) from a backtest payload — **not** from live DB tables, so it can't be reused directly, but it's the right visual/structural pattern to mirror. The live version needs its own loader joining `live.blended_positions` + `live.blended_fills` (grouped by `position_id`, fills ordered by `fill_number`) shaped similarly, likely a new `load_dashboard_blended_positions(model_id)` in `db.py` alongside a new render function (or an adapted call into `_render_blended_trade_log` if the live loader shapes its output to match what that function already expects — worth checking whether that's cleaner than writing a parallel renderer).
- Compounding needs surfacing too — `live.blended_capital.available_capital` is the actual "current capital" number for Model 3, distinct from a fixed `$100` starting capital; the dashboard's capital/equity displays assume a fixed `initial_capital` throughout (works fine for Model 1's flat lot sizing, needs to account for Model 3's growing base).

#### 3. Keep both production models easy to see side-by-side

Both pages need a clean way to flip between (or combine) Model 1 and Model 3 views without the user having to know which table backs which model. Whatever UI pattern gets picked, it should generalize — Model 4+ will hit the exact same "new slot_mode, new live tables" situation eventually if the model tournament keeps producing structurally different strategies.

**Suggested approach for next session:** start with Live Monitor's model toggle (smaller, more contained change), then tackle Model Dashboard's blended live-loader (bigger, more novel — mirrors the Stream Tester blended work from 2026-08-01, same kind of iterative "build → look at real data → fix" loop that took a full session there).

---

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

### Closed — Low-Volatility Uptrend Stream Investigation (2026-07-31)

**Origin:** Ran both Model 1 and Model 2 backtests over July 3 → July 23. Zero trades fired on either model — BTC climbed ~8% on a quiet grind with no sharp dips, breakout squeezes, EMA crossovers, volume spikes, or fear events to trigger any existing stream.

**Investigated:** Built candidate stream **"Quiet Climber"** (`backtest.streams.stream_id=9`) — short-lookback new-high breakout, gated to quiet ATR + trend-above-SMA, so it fires on persistent grinds instead of waiting for an event. Iterated v1→v4 in `backtest.stream_configs` (22-25):
- v1: tight 3.5% trail, low frequency — proved the concept (7 signals in the July dead window) but weak returns
- v2: widened trail to 7% — mixed, no clean win
- v3: shortened lookback + loosened ATR filter for frequency, added `trail_step_tighten` exit — strong overall (23.7% ann. Primary v2, 24.1% ann. Recent) but badly whipsawed in 2026's correction (SMA50 trend filter is a coin flip in a genuine downtrend)
- v4: swapped trend filter to SMA200 (matching every other locked stream's convention) — cut the 2026 damage roughly in half (-12.8% ann vs -29.6%) at some cost to peak-regime upside (15.4% ann Primary v2)

**Verdict: not pursued further.** v4's numbers (15.4% ann Primary v2, PF ~1.3, -30 to -40% max drawdown depending on window) don't clear the bar Model 2's 4 selected streams already cleared. Also confirmed 2026 drag isn't unique to this stream — every momentum/continuation-style stream is bleeding this year (Volume Raider v4: -35.3% ann 2026 YTD) while Dip Hunter (mean-reversion) is having its best year (+71% ann) — expected complementarity behavior in a correction year, not a sign anything is broken.

Config history left in the DB as a record; not assembled into any model.

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
| `executor.yml` | Every 30 min (cron-job.org) | Runs `src.live.executor` tick (Model 1, checks out `live-model-1`) |
| `market_data.yml` | Every 15 min (cron-job.org) | Fetches candles + updates sentiment (shared, both models) |
| `healthcheck.yml` | Every 2h (cron-job.org) | Model 1 dead man's switch — alerts if executor silent > 2h |
| `executor_m3.yml` | Not yet registered on cron-job.org | Runs `src.live.blended_executor` tick (Model 3, checks out `live-model-3` — branch not cut yet). Separate `DRY_RUN_M3` secret. |
| `healthcheck_m3.yml` | Not yet registered on cron-job.org | Model 3 dead man's switch (`src.live.blended_healthcheck`) — independent of Model 1's |

### Alert System
`src/live/notifier.py` (Model 1):
| Function | When It Fires |
|---|---|
| `alert_order_placed()` | Limit buy submitted to Kraken |
| `alert_opened()` | Limit buy filled |
| `alert_closed()` | Trailing stop triggered, position closed |
| `alert_order_expired()` | Order timed out unfilled, slot freed |
| `alert_system_down(hours)` | Executor silent > 2h (executor self-check or healthcheck) — shared by both models |

`src/live/blended_notifier.py` (Model 3 — reuses `notifier._dispatch`, distinct copy):
| Function | When It Fires |
|---|---|
| `alert_blend_order_placed()` | Slot-1 or cascade-add limit buy submitted |
| `alert_blend_opened()` | Slot-1 fill confirmed, position now OPEN |
| `alert_blend_add_filled()` | A cascade add filled, includes new blended avg cost |
| `alert_blend_order_expired()` | Slot-1 or add order timed out unfilled |
| `alert_blend_closed()` | Position closed — copy distinguishes trailing_stop vs. capitulation_stop |

### Model 1 Streams (LIVE)
- **Momentum Rider v2** (stream_id=1) — 4h | EMA 30/120 | 7% trail | $33.33
- **Dip Hunter v2** (stream_id=2) — 1h | RSI recovery, F&G≤20, 25% drawdown, RSI≥35, 10% trail | $33.33
- **Breakout Scout v2** (stream_id=3) — 1h | range_breakout | SMA200 | F&G≥55 | 10% trail | $33.33

### Model 2 Streams — Run 3 SELECTED (BACKTESTED, NOT LIVE)
- **Dip Hunter v3** (config_id=11): rsi_recovery 1h, SL 6%, 1 slot, $25/lot
- **Volume Raider v1** (config_id=10): volume_surge 4h, 1 slot single, $25/lot
- **Breakout Scout v3** (config_id=12): range_breakout 1h, SL 3%, 1 slot, $25/lot
- **Momentum Rider v4** (config_id=16): ema_crossover 4h, single slot, 8% trail, $25/lot

### Model 3 — Grid Stacker Blended (LIVE CODE BUILT + TESTED, NOT DEPLOYED)
- **stream_id=11**, final config **stream_config_id=36 (v8)**, `slot_mode='blended'` — a new engine mode (`_run_blended_slots()` in `src/backtester/engine.py`), not a variant of cascade/staggered.
- Solo stream, uses the model's full $100 (not split across multiple streams like Model 1/2).
- Config: 4h | fear_dip dip_pct=1.0 | 5 slots equal $20 | cumulative_drop_pcts=[1,2,5,10] | trailing_stop_pct=5.0 | trail_arm_gain_pct=4 | capitulation_stop_pct=15 | compound=True.
- Backtested 84.77% ann (Full History), zero real losses except one real capitulation event (Aug 2024, -21.3%, only surfaces at looser trail settings than the final config). Full validation trail in the 2026-08-01 session below.
- Finalized as a proper model: `backtest.models` (model_version=3) + `backtest.model_streams` + `backtest.model_tests.model_test_id=106` (+84.7% ann, 482 trades, matches stream-level number).
- **Live execution code (`src/live/blended_*.py`, `deploy_model3.py`) is built and passing 16/16 tests.** Not yet deployed to Supabase — see "What's Next" above for the remaining steps (fund Kraken, deploy DB rows, cut `live-model-3` branch, register cron-job.org, dry-run trial, then go live).

### App Architecture
- Multipage app: `src/app/app.py` — port 8504 in dev (`streamlit run src/app/app.py --server.port 8504`)
- Pages: Stream Tester, Model Tester, Live Monitor, Model Dashboard
- `src/app/db.py` DB pattern:
  - `get_local_engine()` — always local postgres via `DB_*` env vars — use for all `backtest.*` queries
  - `get_engine()` — uses `SUPABASE_DATABASE_URL` (always Supabase, never local) — use for `live.*` queries only

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

### Live Schema — Model 3 additions (Supabase only, additive to the tables above)
| Table | What it holds |
|---|---|
| `live.blended_positions` | One row per stack (not per fill) — status, avg_cost_basis, total_qty, capitulation_armed, pending-entry/pending-add tracking, position_capital_base |
| `live.blended_fills` | Child rows per fill — fill_number, price, capital, qty, order_id |
| `live.blended_capital` | Model 3's own capital ledger (model_id → available_capital), never Kraken's actual balance |
| `live.executor_state.model_id` | New nullable column — lets Model 1 and Model 3 each own a heartbeat row instead of fighting over the old `id=1` singleton |
| `live.executor_runs.model_id` | New nullable column — tags which model a run log row belongs to |

Migration: `src/data/migration_v4_model3.sql` (applied to both local Postgres and Supabase, 2026-08-02).

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
| `engine.py` (`_run_blended_slots`) | Breakeven floor double-counted the buy fee (already baked into `avg_ep` via reduced qty), landing ~+0.25% instead of true $0 | `breakeven = avg_ep / (1 - fee)` instead of `avg_ep * (1 + fee*2)` |
| `engine.py` (`_run_blended_slots`) | Cascade adds filled instantly at the trigger candle's close, zero simulated latency — unlike slot 1's realistic pending-limit-with-expiry | Route adds through the same `pending_add` limit-order-with-expiry mechanism as slot 1 |
| `model_engine.py` | `initial_capital = lot_size_usd * slot_count` for every slot_mode — wrong for blended, where lot_size_usd already IS total capital (5x inflation) | Special-cased blended mode to use `lot_size_usd` directly |
| `blended_executor.py` | Fallback `INSERT` into `live.executor_state` didn't specify `id`, defaulted to `1`, collided with Model 1's row | Insert with `id = model_id` explicitly |
| `blended_position_monitor.py` | Breakeven floor used `MAKER_FEE` but the real exit is a `TAKER_FEE` market sell — could realize a small real loss | `breakeven = avg_ep / (1 - TAKER_FEE)`, matching `place_exit`'s real pnl formula |
| `deploy_model3.py` | Queried `backtest.stream_configs` over `SUPABASE_DATABASE_URL` — Supabase has no `backtest` schema | Split into a local-Postgres read + a Supabase write, two separate connections |
| `deploy_model3.py` | SQLAlchemy `text()` doesn't safely parse `:name::type` (cast right after a bind param) | `CAST(:params AS jsonb)` instead |
| `blended_order_manager.py` | Cancelling an order on our own expiry then unconditionally deleting the position discarded any partial fill that landed right before cancellation (real BTC bought, silently lost) | Always requery status after cancelling; only free the slot when confirmed `vol_exec == 0` |
| `blended_order_manager.py` | A partial fill on an order still `"open"` (not yet cancelled/closed) was finalized immediately, which would stop polling it and orphan any further fill on the same order | Only finalize on a terminal status (`closed`, or `canceled`/`expired` with `vol_exec > 0`); leave a still-`open` order untouched and poll again next tick |
