# Handoff — 2026-08-02

## ⚠️ Real fees were wrong for every model until 2026-08-03 — now corrected

Kraken's real current fee tier (confirmed live via the `TradeVolume` API, not assumed): **maker 0.40%, taker 0.80%** — double what every backtest and live formula assumed (0.25%/0.40%) since the project started. Fixed in code, all backtests re-run. See the 2026-08-03 "Done This Session" entry below for the full story, including a second, unresolved gap (live code doesn't capture Kraken's *real* per-trade fee at all, only estimates via constants) that needs a dedicated supervised session, not a quick fix.

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

## Done This Session (2026-08-03) — Real Kraken fees were double what every model assumed; corrected everywhere; one deeper gap found and deliberately not fixed

**How this surfaced:** while explaining Grid Stacker Blended's entry-signal mechanics to the user (fear_dip: fires when a 4h close is ≥1% below the *previous* 4h close), the conversation moved to how the limit order's price gets set — `kraken.get_ticker_price()`, the current last-trade price, fetched live at signal-fire time. User confirmed that's the intended design. Then: **Model 3's first real trade filled** (slot 1, $62,822.12, $20 deployed, 2026-08-03 04:01 UTC). Checking Kraken's own trade record for it (`TradesHistory`) turned up two things at once:

1. `"maker": false` — it filled as a **taker**, not maker. Exactly the crossing risk flagged earlier: this strategy only fires *during* a dip, so the "current price" fetched at signal time is systematically likely to be stale-high relative to where the book has already moved, making the buy limit cross the spread.
2. **The fee was $0.16 on a $20.00005 trade — exactly 0.80%.** Double `TAKER_FEE = 0.0040` (0.40%), the constant used everywhere in the codebase.

**Confirmed directly from Kraken, not inferred:** `kraken._api.query_private('TradeVolume', {'pair': 'XXBTZUSD'})` reports this account's real current tier: **maker 0.40%, taker 0.80%** (`tiervolume: 0`, `nextvolume: 2500` — fees step down once 30-day trading volume crosses $2,500; taker drops to 0.60% at minimum at that point, re-check rather than assume when it does). Every backtest for every model, and the live breakeven-floor/P&L-estimate formulas, had been using 0.25%/0.40% — a schedule this project's actual low-volume accounts were never on.

**Fixed:**
- `MAKER_FEE`/`TAKER_FEE` corrected to 0.0040/0.0080 in `src/backtester/engine.py`, `src/live/order_manager.py` (single source of truth — `blended_order_manager.py`/`blended_position_monitor.py` import `TAKER_FEE` from here, picked up automatically), and the display-only copy in `src/app/dashboard.py`. `tests/live/` (27 tests) still green — no test had a hardcoded expected number tied to the old fee values, all computed dynamically off the imported constants.
- **Two more real, unrelated bugs found while re-running backtests to get an accurate before/after:** `backtest.model_streams` had drifted from reality for two of three models. Model 1's was still at the `lot_size_usd` **default of $10**, never updated to the real deployed **$33.33/stream** — meaning no model-level backtest had ever actually reflected Model 1's real live allocation, fee assumption aside. Model 2's Momentum Rider row was linked to the **wrong stream_config entirely** (v3 staggered $12.50) instead of Run 3's actual selected composition (v4 single $25, confirmed against the `configuration` JSON saved with that run). Both corrected directly in the DB (backtest schema, freely rebuilt, not live).
- **Re-ran all three models across all 5 presets** (new script: `src/backtester/rerun_corrected_fees.py`) with corrected fees and corrected compositions, saved as new `model_tests` rows (old rows kept for history, not deleted):

  | Model | Preset | Old ann% (wrong fee, and for M1/M2 also wrong allocation) | New ann% (corrected) |
  |---|---|---|---|
  | Model 1 (live) | Full History | +22.2% | +20.2% |
  | Model 1 (live) | Primary v2 | +15.6% | +13.6% |
  | Model 2 (Run 3) | Full History | +21.7% | +19.4% |
  | Model 2 (Run 3) | Primary v2 | +19.2% | +16.9% |
  | Model 3 (Grid Stacker) | Full History | +84.7% | **+71.7%** |
  | Model 3 (Grid Stacker) | Primary v2 | +54.3% | +41.5% |

  Model 3 moved the most (trades far more often, so fee drag compounds harder) — and its Full History **max drawdown went from -0.0% to -20.9%**: several trades that looked flat (~$0 P&L) under the old assumption were actually small real losses once taxed at the real rate. Full per-preset numbers in `backtest.model_tests` (Model 1: ids 111-115, Model 2: 116-120, Model 3: 121-125), all tagged with a `notes` field explaining the correction.

- **Docs updated**: `CLAUDE.md`'s "Key Constraints" now says fees are tiered/confirmed-live rather than a fixed assumed number, and points at where to re-check. Memory (`project-live-deployment`, `project-core`) updated with the real numbers and the corrected Run 3 comparison.

**Found, explained, and deliberately NOT fixed tonight — needs a dedicated supervised session:** live code never captures Kraken's *actual* per-trade fee or fill price at all. `kraken_client.py`'s `get_order_status()` discards the `cost`/`fee` fields Kraken's own API returns (confirmed present in both `QueryOrders` and `TradesHistory` responses) and only surfaces `status`/`vol_exec`/`price`. This means:
  - Every live P&L number, for both Model 1 and Model 3, is still an *estimate* off the `MAKER_FEE`/`TAKER_FEE` constants — never ground truth from the exchange, even after tonight's fix. The constants are now the *right* estimate, but they're still an estimate, and per-trade real maker/taker outcome varies (as trade #1 already showed).
  - `blended_order_manager.py`/Model 1's `order_manager.py` record `capital`/`opening_capital` as the *intended* allocation ($20.00, $33.33, etc.), not the *real* USD debited (`cost + fee` — $20.16005 for trade #1). The backtester correctly nets the buy-side fee out of BTC quantity (`qty = capital*(1-fee)/price`); live does not do the equivalent anywhere, because `vol_exec` (real, from Kraken) is used directly for quantity while `capital` stays at the pre-fee target. Net effect: the buy-side fee is never subtracted from anything, anywhere, in live code — cost basis and realized P&L are both silently overstated by it.
  - Model 1's exit price is *also* an estimate (`exit_price = current_price`, the candle close) rather than Kraken's real average fill price for the market sell — the code comment says "actual fill price resolved on next check" but nothing currently does that reconciliation.
  - **Why not fixed tonight:** this touches the core fill-recording state machine for *both* currently-live models, each with a real open position right now. The user was stepping away for the night ("deal with fallout tomorrow") when this was found — a correctness fix to the constants (safe, tested, backtest-only + estimate-formula impact) is a very different risk profile than rewiring how real fills get recorded for two live systems simultaneously, unsupervised. Recommend a focused future session: read real `cost`/`fee` from Kraken at every fill (entry and exit, both models), use them directly instead of any assumed constant, and reconcile Model 1's exit price the same way. Watch the next several real fills closely once that lands.

---

## Done This Session (2026-08-02, later) — market_data freshness guard + Live Monitor multi-model support

**1. Closed a real timing race in both executors.** Discussed with the user: the executor trusted a closed 1h/4h candle boundary purely on wall-clock time, but `resample_ohlcv`'s `dropna()` only drops fully-empty bins — a bin missing its last 15m bar still produces a candle, just from incomplete data. If `market_data_updater`'s cron hadn't landed that bar yet when an executor ticked, a signal or trailing-stop check could fire off a wrong close/high/low. Considered and rejected two alternatives first: bumping market_data's cron frequency (only shrinks the window, doesn't close it) and a `workflow_call` job dependency (only guarantees ordering, not that the data is actually there). Landed on `_ensure_market_data_fresh()` (shared by `executor.py` and `blended_executor.py`): blocks up to 3× 30s retries for `market_data` to catch up, then raises `MarketDataStaleError` + sends a real email/SMS alert (`notifier.alert_market_data_stale`) rather than logging and moving on — the transaction rolls back so `last_run_at` never advances, meaning the next tick retries the same boundary instead of silently skipping it.

Ported to all three branches: `main` (`dd6efec`), `live-model-1` (`1b6a9e3`, minimal port — left the branch's existing `_preflight_check` untouched per explicit user call, even though it has a known `last_run_at`-advances-on-failure bug; not in scope today), `live-model-3` (fast-forwarded to `main`'s tip, `034f7cb`... `git push origin main:live-model-3`, had to be run by the user directly — the harness's permission classifier blocked Claude pushing to a branch other than the one checked out, twice, even though it was a pure fast-forward).

**2. Live Monitor (`2_live_monitor.py`) — three small display fixes**, then the bigger multi-model piece:
- "Last candle" line and System Status's last-run timestamps now show Central Time (`_fmt_central()`, `zoneinfo.ZoneInfo("America/Chicago")`) instead of UTC.
- Open Positions gained a **Current Price** column (latest `market_data` close) next to Entry Price.
- **Model 3 is now visible** — a `st.radio` toggle at the top switches between Model 1 and Model 3 (`SELECTED_MODEL_ID` / `IS_BLENDED`). Model 1's `live.lots`-shaped queries all gained a `model_id` filter they never had before (previously `load_stream_status()` had **no model filter at all** and was already silently mixing Model 3's stream into what looked like Model 1's Stream Status section — this fixes that too, not just adds Model 3's own view). Model 3's branch reads `live.blended_positions` + `live.blended_fills` + `live.blended_capital` directly — new loaders `load_blended_positions(model_id, status)`, `load_blended_fills(position_ids)`, `load_blended_capital(model_id)` — and renders the blended stack shape (avg cost basis, total qty, capitulation-armed flag, nested fills expander) instead of Model 1's one-row-per-slot lot table.
- Added a `fear_dip` condition block to Stream Status's core-signal rendering (previously only `ema_crossover`/`rsi_recovery`/`range_breakout` were handled — Model 3's own signal type had no visual condition at all, mirrors the real check in `src/backtester/signals.py` exactly: fires when close drops `dip_pct`% below an SMA or the previous candle's close).
- One gotcha ported into the fix: `live.executor_runs` rows have `model_id IS NULL` for Model 1 (its `executor.py` never sets the column, predates multi-model support) vs `model_id = 3` for Model 3 — the loader branches on this explicitly rather than assuming `model_id = 1` would match.
- **Two bugs found afterward, both fixed same session:** (1) three new captions with two literal `$` each (`Compounding capital`, `Avg Entry/Current`, `HWM/Trail stop`) tripped Streamlit's markdown-as-LaTeX auto-conversion between dollar signs, garbling the text (e.g. "started at100") — fixed by escaping to `\$`. (2) all the run-log tables (Executor Run Log, Market Data Run Log, Opened/Placed/Closed columns) were still in UTC despite the earlier Central Time pass only covering the "Last candle" line and System Status — swept the whole page to `_fmt_central()`.
- Confirmed (not a bug): System Status's "Last Executor Run" looking identical between Model 1 and Model 3 toggles is real, correctly-separated data (1,450 rows since July 3 vs. 8 rows since Aug 2, verified via direct query) — both cron jobs just happen to fire within seconds of each other on the same 30-min cadence.

**Feedback from user this session:** don't launch the Streamlit app / drive it with Playwright to self-verify UI changes — user tests visually themselves. Saved to memory (`feedback_no_auto_ui_testing.md`).

---

## Done This Session (2026-08-02, even later) — Model Dashboard now supports Model 3; two more real bugs found and fixed

**1. Model Dashboard sidebar was labeling Model 3 as "Model 4."** `backtest.models.model_id` (internal serial PK) and `model_version` (the actual "Model N" business label) coincide for Models 1-2 by chance (both 1=1, 2=2) but not Model 3 (`model_id=4`, `model_version=3`, since a since-removed test row consumed id 3). The selector at `3_model_dashboard.py` built its label from `model_id` instead of `model_version` — fixed to use `model_version` for display while `model_id` keeps flowing through unchanged to every downstream query.

**2. Model 1 Live crashed the Model Dashboard's Stream Status** with `StreamlitInvalidColumnSpecError` from `st.columns(len(stream_names))`. Root cause: `stream_names` was derived only from **closed** trades (`closed["full_stream_name"].unique()`), and Model 1's live data currently has exactly one lot — status `OPEN`, zero `CLOSED` — so the list was empty and `st.columns(0)` isn't valid. Fixed to derive from all lots regardless of status (`lots["full_stream_name"].unique()`), so a stream with only an open position still gets its own card.

**3. Backfilled all 5 timeframe presets for Model 3.** `finalize_model3.py` only ever ran "Full History" — the only saved `backtest.model_tests` row for Model 3, vs. Models 1/2 which have all 5. New one-time script `src/backtester/backfill_model3_presets.py` ran the remaining four (Primary Window +98.5% ann, Recent +61.8%, 2026 YTD +25.0%, Primary v2 +54.3% — Full History stays +84.7%, unchanged) and saved each; all correctly landed as `run_number=1` since `next_model_run_number()` matches by allocation hash and Model 3's solo-stream config never changes across presets. Also changed the dashboard's "Backtest run" selector to default to the most recent "Full History" run instead of whatever sorted first.

**4. Model Dashboard's Live source was fundamentally broken for Model 3 — two compounding bugs, not one.** `load_dashboard_lots(model_id, "live", None)` passed the sidebar's `model_id` (a `backtest.models.model_id`, e.g. 4) straight into a query against `live.lots`/`live.streams`, which are keyed by the **unrelated** `live.models.model_id` (3, for Model 3) — the same id-space confusion as bug #1, just in the live path this time, and it coincidentally worked for Model 1 only because `backtest.models.model_id=1` happens to equal `live.models.model_id=1`. On top of that, Model 3 doesn't even use `live.lots` — its data lives in `live.blended_positions`/`live.blended_fills`. Fixed: added `_live_model_id_for_version()` to resolve the correct id via the shared `model_version` before any live query, and a new `_load_dashboard_blended_lots_live()` that shapes `live.blended_positions` + `live.blended_fills` into the same per-trade row shape the rest of the dashboard already expects (one row per blended position standing in for one row per lot), so none of the equity curve / monthly P&L / open-positions code downstream needed to change.

**5. Added a Stream-Tester-style hierarchical "Blend N" trade log** for blended models (`_render_blended_dashboard_log()` in `3_model_dashboard.py`, gated on a new `is_blended` flag). Live data gets **real nested fills** (from `live.blended_fills` — genuinely not a lossy view); backtest data gets the rolled-up summary only, since `backtest.lots` only ever persisted one row per finished blend, never the per-fill breakdown (that only ever existed in the ephemeral backtest-run payload Stream Tester renders in-session). Deliberately did not recompute a fee breakdown here (unlike Stream Tester's version) — backtest uses maker-fee-both-sides simulation, live uses real taker-fee exits, and the two shouldn't be mixed; P&L/closing capital are trusted as-is from whichever system computed them.

**6. Found and fixed a real `total_capital` bug while verifying #5 against the known-good headline number.** The dashboard's generic capital calc (`first_per_slot["opening_capital"].sum()`, first-closed-trade-per-slot) assumes a fixed lot size per slot — true for Models 1/2, false for a compounding blended position where `opening_capital` is just whatever actually got deployed (1-5 of the 5 slots) before that position closed, not the frozen pool size. This would have silently wrecked every %-of-capital and annualized-return number on the page for Model 3. Fixed with a new `load_blended_starting_capital()`: backtest reads `backtest.model_streams.lot_size_usd` directly (it already **is** the pool size); live backs it out as `available_capital − realized_pnl of all closed positions` (no historical seed is persisted, only the current compounded value). Verified the fix reproduces the known-good headline exactly: `$19,275.23 pnl / $100 capital / 3,134 days → 84.7% ann`, matching the original validated number from 2026-08-01 to one decimal place.

**7. Replaced "Stream Status" with "Slot Status" for blended models.** User's own observation: a solo-stream cascade model isn't "streams," it's slots — the old per-stream card view had nothing meaningful to say about a single stream. New `_render_slot_status()`: a **Current Position** panel (slots filled N/5, capital deployed vs. pool, blended avg cost, unrealized P&L, trail stop, a capitulation-armed warning once all slots fill) plus a **historical slot-depth breakdown** — win rate / avg return / avg hold time bucketed by how many slots each closed position used, a bar chart of the depth distribution, and aggregate stats (avg slots used, % fully maxed out, trailing-stop vs. capitulation-stop exit counts). Needed two small additive columns in `load_dashboard_lots` to support it: `slot_count` (the real total, not hardcoded 5) and `capital_base` (the frozen pool size for the position, live-only — backtest never persisted it separately from what got deployed). Also caught and fixed a units mismatch while building this: the live blended loader had mapped `opening_capital` to `position_capital_base` (the full frozen pool) instead of actually-deployed capital, inconsistent with backtest's semantics and understating unrealized-% for any position that hadn't filled all 5 slots — fixed to sum actual fill capital, matching backtest exactly, with the frozen pool now carried separately as `capital_base`. Dry-run verified against Model 3's Full History backtest: win rate drops from 72% (1 slot used) to 33% (all 5 slots used) — a real, sensible pattern, not noise.

**Also fixed:** a pre-existing latent bug in the equity curve tabs (`_equity_chart`), exposed for the first time by the new "2026 YTD" preset — when a run's full trade history already starts in 2026 (as "2026 YTD" naturally does), the "All Time" and "YTD" tabs render identical charts, and `st.plotly_chart` had no explicit `key`, so Streamlit's auto-generated-ID collision detection raised `StreamlitDuplicateElementId`. Fixed by keying each tab's chart on its label.

---

## Done This Session (2026-08-03) — Model 1 executor timed out; real gap in the freshness-guard fix, not the guard itself

User caught a live `executor.yml` run ([run 30780654083](https://github.com/Reverie-Revival/forge-anchor-collective/actions/runs/30780654083/job/91584474700)) that hit the job's 5-minute timeout and got force-killed, right after the market_data freshness guard shipped, and asked whether the two were related.

**Diagnosis via the actual GH Actions log** (not guessed): total silence between `Kraken connected` (03:00:37) and the forced `The operation was canceled` (03:05:27) — no retry-warning lines from the new guard, but also no `Loaded N streams`, which is logged *before* the guard ever runs. So it wasn't the guard's retry loop (which logs on every attempt) — something hung on the very first, unlogged step: acquiring the DB connection or running `_load_streams`'s query, neither of which had ever had a timeout configured.

**Root cause: `_get_engine()` had zero connection or statement timeout, on any branch, before or after today's other changes.** A stalled connection or a slow/locked query blocks forever — or at least until the CI job's own 5-minute budget kills it — with no error, no log line, nothing. This was always latent; the freshness guard didn't cause it, it just happened to be the run where something (network blip, Supabase pooler contention — exact trigger unconfirmed and likely unknowable after the fact) finally stalled long enough to hit it.

**Fixed in `executor.py` and `blended_executor.py`** (both `main` and `live-model-1`'s independent copy): `create_engine(url, connect_args={"connect_timeout": 10})` bounds connection acquisition to 10s; `conn.execute(text("SET statement_timeout = '15s'"))` right after `engine.begin()` bounds every query in the transaction to 15s. A stuck connection or query now fails loudly (clear exception, job fails fast and visibly) instead of hanging silently for the full job timeout. 15s is generous for every query this executor actually runs (all single-table, low-row-count lookups) but far short of the 5-minute budget.

Ported to `main` and `live-model-1` (`live-model-3` gets it for free via fast-forward, same as the freshness guard). Full `tests/live/` suite (27 tests) still green on both after the change.

**Also, while explaining Grid Stacker Blended's entry signal to the user (fear_dip, no sma_period → fires when a 4h close is ≥1% below the *previous* 4h close, no other filters), added a "Slot Status" ladder to Live Monitor for Model 3** (`_render_slot_ladder()` in `2_live_monitor.py`) — replaces the single generic condition card in Section 3 with all 5 cascade slots shown individually: filled (real price/capital/timestamp from `live.blended_fills`), order placed (real expiry from `pending_entry_expiry_at`/`pending_add_expiry_at`), or not yet triggered (the actual `cumulative_drop_pcts`-derived trigger price and live distance from it). When no position is open at all, Slot 1 shows the real fear_dip progress bar (reusing the existing condition data) and Slots 2-5 show "waiting on Slot 1." Needed one new column (`pending_add_index`, to know which slot a pending cascade-add order belongs to) and one new loader (`load_blended_stream_params()`, for `cumulative_drop_pcts`/`slot_count`, which `load_stream_status()` doesn't expose).

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

### 🎯 TOMORROW — Build a fee-drift detector; also decide on the live-branch fee fix + the deeper fee-accounting gap

Three things stacked up from tonight's fee discovery (see "Done This Session (2026-08-03)" above for full context):

1. **Build a fee-drift safeguard.** The 0.25%/0.40% assumption sat wrong in the code for the entire project until a real trade's numbers didn't match — nobody had ever actually queried Kraken's real fee tier against the constants in code. Build something that queries `kraken._api.query_private('TradeVolume', {'pair': 'XXBTZUSD'})` and compares the returned `fee`/`fees_maker` against `MAKER_FEE`/`TAKER_FEE` in `order_manager.py`, and loudly warns (alert, not just a log line) if they've drifted apart. Natural home: either a new check inside `healthcheck.py`/`blended_healthcheck.py` (already runs every 2h, already has alert plumbing), or a small standalone script run periodically. Fees are tiered by 30-day volume (`nextvolume` in the API response) so this isn't a one-time fix — it needs to keep checking as trading volume grows and the tier changes.
2. **Decide whether to push last night's fee-constant fix to `live-model-1`/`live-model-3`.** It's on `main` only right now — deliberately held back because it changes the breakeven-floor calculation for both currently-live models' real open positions, and the user was asleep to watch the next tick. Needs an explicit go-ahead, not a default push.
3. **The deeper live fee-accounting gap** (live code never captures Kraken's real per-trade fee/fill-price, only estimates via constants — see last night's entry for full detail) still needs a dedicated supervised session. Not started.

---

### Both dashboards now support Model 3 — Live Monitor and Model Dashboard

Live Monitor (model toggle) and Model Dashboard (blended trade log + fixed live loader + fixed capital calc) both done — see "Done This Session" entries above for what changed in each. Nothing mandatory left from the original gap analysis.

**Known, deliberate limitation:** Model Dashboard's blended trade log only has real nested per-fill detail for **live** Model 3 data — backtest history only ever persisted the rolled-up blend (`backtest.lots` has no per-fill table), so backtest "Blend N" cards show summary metrics only, no nested fills sub-table. Extending `backtest.lots` (or a new `backtest.lot_fills` table) to persist fill-level detail on future backtest saves would close this, but wasn't done — no backtest run has needed that granularity yet, and it'd touch `_save_lots()` for every model, not just Model 3.

**Not addressed:** a unified UI pattern for switching models that generalizes past two (Model 4+ will hit the same "new slot_mode, new tables" situation again) — Live Monitor's toggle and Model Dashboard's selector are two different, purpose-built solutions, not a shared abstraction. Revisit if/when a Model 4 actually arrives with its own new shape.

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
