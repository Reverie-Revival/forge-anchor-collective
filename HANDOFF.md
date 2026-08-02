# Handoff — 2026-08-01

---
## ⚠️ ACTION REQUIRED — ORACLE ACCOUNT (deadline is TODAY, Aug 1)
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Log into your credit card TODAY and confirm zero Oracle charges ever appeared.** The account should be fully deleted by now — verify it's gone and no recurring relationship exists. This has been reminded every session since the deletion was submitted — please confirm and this banner comes down.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE** — executor running, cron on schedule. Full alert coverage active (order placed, filled, closed, expired, system down).

**Model 2 is assembled and backtested.** Run 3 selected as deployment config. Not yet deployed — deprioritized in favor of Model 3 (see below), which tested significantly stronger.

**Model 3 candidate is backtest-validated and ready for live-code build.** "Grid Stacker Blended" — a blended-average DCA stream — backtested at 24-38% annualized across every window tested, zero real losing trades in 8 years including both major historical crashes, passed rigorous walk-forward and bootstrap validation. **No live execution code exists for it yet — that's tomorrow's entire session.** Full detail below and in Reference section.

**Model Dashboard is BUILT** — `3_model_dashboard.py` live in the multipage app (port 8504).

---

## Done This Session (2026-08-01) — Model 3 candidate discovered, tuned, and QA'd

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

### 🎯 TOMORROW'S SESSION — Build live execution for Model 3 (Grid Stacker Blended)

**Goal stated by user:** get this deployable to Kraken as a second working branch, live, with real $100 — "tomorrow or the next day." Explicitly **not** interested in a formal weeks-long paper-trading phase; wants the live code built with real care and at minimum a short live-logging trial before connecting real orders, given this is genuinely new code, not a config change.

**The honest scope — this is a real build, not a config flip.** An investigation this session (read-only audit of `src/live/`) found:

- **`live.lots` today = one entry-to-exit trade, period.** No cost-basis-across-fills concept, no parent/child relationship between fills, no "fill" sub-entity. One row IS one fill.
- **Model 1's live code has never actually exercised multi-slot logic at all**, despite `slot_count`/`slot_mode` columns existing on `live.streams`. `order_manager.place_entry()` hardcodes `slot_number=1`. `executor.py`'s `slot_is_available()` call is hardcoded to `slot_number=1`. All three live Model 1 streams were seeded with `slot_count: 1`. So "replicate Model 1's branch" does **not** hand you multi-slot handling — that part was never built there either.
- **`signal_engine.py` is stateless w.r.t. open positions** — no access to an open position's cost basis, no way to compare current price to an existing entry. The blended design needs this for both cascade-add triggers and the exit logic.
- **Estimated ~60-70% new code.** Reusable: Kraken order plumbing (`kraken_client.py`), `notifier.py`, the general PENDING→OPEN polling pattern in `executor.py`. Not reusable as-is: `live.lots`' one-row-one-trade schema, `order_manager.py`'s single-lot state machine, `position_monitor.py`'s per-lot-independent stop check, `signal_engine.py`'s lack of position-state awareness.

**Concrete build checklist for tomorrow:**

1. **New table(s)** — most likely `live.blended_positions` (one row per open/closed position: status, original_entry_price, avg_cost, total_qty, capitulation_armed, etc.) + `live.blended_fills` (child rows: fill_number, price, capital, qty, filled_at, order_id). Retrofitting `live.lots` was considered and rejected — too many existing queries (`position_monitor.check_all`, `order_manager.check_pending`, the `reporting.all_lots` view) assume one row = one full trade; a migration there is riskier than a clean new table.
2. **Port `_run_blended_slots`' state machine from batch to incremental.** The backtester loops over all historical candles at once; live needs the equivalent logic rewritten to advance **one tick at a time**, with all state (fills so far, avg cost, whether capitulation is armed, current trailing stop level) persisted in the DB between executor runs (every 30 min via cron).
3. **New order-placement logic** — place a real Kraken limit order for slot 1 on signal, for each cascade add when its threshold is crossed, and for the exit (selling the *combined* quantity across however many fills happened). Compounding needs the live position size computed from actual realized account balance, not a hardcoded number.
4. **Capitulation stop as a real order type** — needs to be distinguished from the normal trailing-stop exit in whatever alerting/logging happens, same as the backtester's `exit_reason` field does.
5. **Wire into `notifier.py`** — new alert copy for blended-specific events (an "add" firing is different from a fresh entry; a capitulation exit should probably get its own distinct, unambiguous alert given what it means).
6. **Register a `live.models` entry** once the code is ready — this part is trivial (a few minutes of bookkeeping), explicitly NOT the hard part, don't let it feel like a blocker.
7. **Before connecting real orders:** run it for at least a few days logging what it *would* do against live prices, to catch "we forgot to handle X" bugs before they cost anything. Short, not a formal multi-week paper-trading program — just enough to not be flying blind on brand-new order-placement code.

**Final validated params to build against** (don't re-derive — this is settled, see "Done This Session" above for the full validation trail):
```
primary_timeframe: 4h | fear_dip dip_pct=1.0 | 5 slots, $20 equal weight (compounds from there)
cumulative_drop_pcts: [1, 2, 5, 10] | trailing_stop_pct: 5.0 | trail_arm_gain_pct: 4
capitulation_stop_pct: 15 | compound: True
```
`backtest.stream_configs.stream_config_id = 36` (v8) has this exact config with full notes if anything needs to be cross-checked.

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
| `executor.yml` | Every 30 min (cron-job.org) | Runs `src.live.executor` tick |
| `market_data.yml` | Every 15 min (cron-job.org) | Fetches candles + updates sentiment |
| `healthcheck.yml` | Every 2h (cron-job.org) | Dead man's switch — alerts if executor silent > 2h |

### Alert System (`src/live/notifier.py`)
| Function | When It Fires |
|---|---|
| `alert_order_placed()` | Limit buy submitted to Kraken |
| `alert_opened()` | Limit buy filled |
| `alert_closed()` | Trailing stop triggered, position closed |
| `alert_order_expired()` | Order timed out unfilled, slot freed |
| `alert_system_down(hours)` | Executor silent > 2h (executor self-check or healthcheck) |

### Model 1 Streams (LIVE)
- **Momentum Rider v2** (stream_id=1) — 4h | EMA 30/120 | 7% trail | $33.33
- **Dip Hunter v2** (stream_id=2) — 1h | RSI recovery, F&G≤20, 25% drawdown, RSI≥35, 10% trail | $33.33
- **Breakout Scout v2** (stream_id=3) — 1h | range_breakout | SMA200 | F&G≥55 | 10% trail | $33.33

### Model 2 Streams — Run 3 SELECTED (BACKTESTED, NOT LIVE)
- **Dip Hunter v3** (config_id=11): rsi_recovery 1h, SL 6%, 1 slot, $25/lot
- **Volume Raider v1** (config_id=10): volume_surge 4h, 1 slot single, $25/lot
- **Breakout Scout v3** (config_id=12): range_breakout 1h, SL 3%, 1 slot, $25/lot
- **Momentum Rider v4** (config_id=16): ema_crossover 4h, single slot, 8% trail, $25/lot

### Model 3 Candidate — Grid Stacker Blended (BACKTESTED, NO LIVE CODE YET)
- **stream_id=11**, final config **stream_config_id=36 (v8)**, `slot_mode='blended'` — a new engine mode (`_run_blended_slots()` in `src/backtester/engine.py`), not a variant of cascade/staggered.
- Solo stream, uses the model's full $100 (not split across multiple streams like Model 1/2).
- Config: 4h | fear_dip dip_pct=1.0 | 5 slots equal $20 | cumulative_drop_pcts=[1,2,5,10] | trailing_stop_pct=5.0 | trail_arm_gain_pct=4 | capitulation_stop_pct=15 | compound=True.
- Backtested 84.77% ann (Full History), zero real losses except one real capitulation event (Aug 2024, -21.3%, only surfaces at looser trail settings than the final config). Full validation trail in "Done This Session" above.
- **No live execution code exists for this slot_mode.** See "TOMORROW'S SESSION" above — this is the entire scope of the next session.

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
