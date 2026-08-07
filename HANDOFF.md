# Handoff — 2026-08-06 (late evening)

## 🔴 START HERE (new session): Model 3 is sold out and purged from the live DB; `live-model-3` branch renamed to `archive/live-model-3`. Tomorrow's real work: (1) finalize + deploy Model 2 into that now-empty infra slot, (2) build and test REAL compounding for Model 2 (currently doesn't exist for its slot modes -- a post-hoc estimate suggests it would hurt, but that needs verifying with a real implementation, not just an estimate), (3) if compounding doesn't help, test the "gains bucket" alternative instead. Model 3/4 redesign continues separately on `experiment/model3-4-redesign`, user frustrated with the lack of a working result there tonight.

### The decision, plainly, and what's actually already done

Tonight's session did three things: (1) a deep redesign/debug pass on Model
3/4 (GS: Phoenix) on `experiment/model3-4-redesign` -- real bugs found and
fixed, still not deployment-ready, see below; (2) discovered Model 1/2's
individual stream + model-level backtest numbers were stale (predated the
2026-08-05 engine fixes), re-ran and re-validated all of them on `main`;
(3) acted on the result -- **Model 2 (9.06%→11.50% ann. beats Model 1 on
Primary v2, similar drawdown, carries Volume Raider, the single best
stream in the whole lineup) is being deployed for real, replacing
`live-model-3`.**

**Already done tonight, not just decided:**
- User manually sold `live-model-3`'s open position on Kraken (real fill:
  0.00031836 BTC @ $64,128.25, $0.30 fee, $20.11 net -- a small real LOSS
  vs. the $20.16 original cost, not the gain it looked like on paper
  minutes earlier; a real fee-drift check was run against live Kraken and
  confirmed the 0.40%/0.80% fee tier is still accurate, so this wasn't a
  stale-fee-constant problem, just real execution/small-trade-size noise).
- Cron jobs paused (`cron-job.org`, external to this repo -- can't be
  toggled from here).
- **Model 3 fully purged from the live Supabase database** -- `live.models`,
  `live.streams`, `live.blended_positions`, `live.blended_fills`,
  `live.blended_capital`, `live.executor_state`, all 194
  `live.executor_runs` rows for model_id=3, all deleted. Only Model 1
  remains in `live.models` now.
- `live-model-3` branch renamed to **`archive/live-model-3`** (both local
  and on origin, old name fully removed). Checked first whether it had
  anything not already on `main` -- it didn't (only 2 commits ahead of a
  much-earlier `main`, both already independently present on current
  `main`; 33 commits behind). **When Model 3 comes back for real, cut a
  fresh branch off `main` at that time -- don't resurrect this one.**
- `live-model-1` is untouched, still running as-is, unaffected by any of
  this.

### What to actually do next session (the Model 2 deployment)

This is real money / real infra work, not yet started:
1. **Repurpose the now-empty infra slot for Model 2** -- workflow rename
   (`executor_m3.yml`/`healthcheck_m3.yml` → something Model-2-appropriate),
   `LIVE_MODEL_VERSION` update, fresh `live.models`/`live.streams` rows for
   Model 2's real composition (Momentum Rider v4 $25 + Dip Hunter v3 $25 +
   Breakout Scout v3 $25 + Volume Raider v1 $25 = $100), fresh capital
   ledger entry.
2. Model 2 is **single/staggered/scale_up mode only** (no blended-mode
   streams) -- none of tonight's blended-specific bracket fixes apply to
   it. It should already be running on whatever `live-model-1`-equivalent
   single-slot live code path Model 1 uses today (untouched, unaffected by
   any of tonight's blended-only work) -- confirm that code path is
   current/correct before deploying, same diligence as any live change.
3. Full validated Model 1 vs Model 2 comparison (Primary v2, current/
   honest numbers, for reference when doing this):

| | Model 1 | Model 2 |
|---|---|---|
| Annualized Return | 9.06% | **11.50%** |
| Max Drawdown | -16.05% | -16.25% |
| Total Trades | 67 | 108 |
| Win Rate | 40.3% | 41.7% |
| Ending Balance ($100 start) | $148.86 | **$164.71** |

   Streams inside each (all re-validated this session, current on `main`):

| Stream | Model | Ann. Return | Max DD | Win Rate |
|---|---|---|---|---|
| Volume Raider v1 | 2 | **18.68%** | -27.80% | 46.2% |
| Momentum Rider v2 | 1 | 11.52% | -20.85% | 32.3% |
| Breakout Scout v3 | 2 | 11.26% | -25.45% | 30.0% |
| Momentum Rider v4 | 2 | 9.03% | -21.20% | 34.5% |
| Breakout Scout v2 | 1 | 8.27% | -28.14% | 31.3% |
| Dip Hunter v2 | 1 | 7.24% | -30.23% | 60.0% |
| Dip Hunter v3 | 2 | 5.48% | -28.51% | 55.0% |

   Volume Raider v1 is clearly carrying Model 2. Also found (not yet
   pursued): a NOT-yet-deployed version, Volume Raider v4, backtests even
   stronger (22.80% ann., Primary v2) -- a real candidate to swap in later,
   but not validated for this deployment; ship the already-proven v1
   composition first, revisit v4 as a future stream-config upgrade once
   locked and properly tested standalone.

### Why the Model 1/2 numbers changed (resolved, don't re-litigate)

Model 1/2's `backtest.stream_tests` rows were last saved 2026-08-03,
predating the 2026-08-05 engine fixes (`e1cea7a` one-tick entry-fill delay,
`6730ec3` staggered/cascade exit-fill optimism) -- both apply to
single/staggered/scale_up mode (what Model 1/2 actually use). Re-ran all 16
stream configs × 5 presets = 80 rows on current `main`; nearly every number
moved, mostly down (e.g. Volume Raider v3 looked like a clear standout at
31.03% ann., corrected to 17.13% -- no longer better than what's already
deployed). `backtest.model_tests` for Models 1/2/3 were ALREADY current
(saved same fix-session, 2026-08-06 early morning, model_test_id 127-136,
142-146) -- did not need re-running. **Model 4 has NO saved model_test at
all** -- `backtest.model_streams` has two conflicting rows for
model_version=4 (v1 and v2 both attached), blocking the official save
path. Real, still-open, unrelated to tonight's other work.

### GS: Phoenix (Model 3/4 redesign) status -- separate track, still in progress, user frustrated with the lack of a working result

Full technical detail in `docs/decisions/008-gs-phoenix-redesign.md` on
`experiment/model3-4-redesign` (merged up to date with `main`). Real fixes
made and kept: a genuine stop-anchor bug in the slot-5 bracket (was
anchored to average cost, not current price -- stop was already breached
before the bracket even went live, target could never fire), real
30-minute sub-candle exit resolution (matches live's actual poll cadence),
a last-fill-anchored arm/trail alternative to the bracket, a re-entry
cooldown. All tested (49/49 passing), all opt-in/additive.

**Despite all of that, no variant tested tonight is net positive on the
full Primary v2 window (2022-present).** Tried, in order, after the fixes
above: tuned bracket stop/target distances (best: -2.02% ann, stop=11%),
no bracket at all / normal arm-trail fallback (-2.13% ann, 100% win rate
on closed trades but only 36 trades total), a 4-slot "combo last slot"
redesign at 2/5/10% cascade spacing with a doubled final slot (-11.59%
ann, 27% of trades hit the forced-exit slot), the same redesign at wider
spacing 4/9/18% (best of that family: -4.72% ann), and a "pyramid" capital
weighting ($10/$20/$30/$40 per slot instead of equal, 5/10/20% spacing)
which gave the best RISK profile of the whole session (-3.30% ann but only
-18.98% max DD, vs. -32% to -60% for everything else) without beating the
best return. **User's own assessment, stated directly: frustrated this
doesn't work, may come back to it later.** Not touched again this session
after that. Also still queued, not started: a better re-entry filter than
the blanket SMA200 gate (blocks real dip-buying opportunities, not just
falling knives), and Stream Tester still stale for Grid Stacker Blended /
GS: Reflex.

### Model 2 compounding + "gains bucket" -- new, queued for next session, NOT YET BUILT

User's real question after seeing Model 2's validated numbers: Model 2
doesn't compound (confirmed by reading the code -- `compound` is only
implemented in `_run_blended_slots`, doesn't exist at all for
single/staggered/scale_up, the modes Model 1/2 actually use -- not just
"off", not currently possible without new engine work). Estimated the
effect via a post-hoc reconstruction (same real trade sequence/% returns,
replayed with position size scaled to running balance instead of a fixed
$25/stream): **compounding would have made Model 2 WORSE on Primary v2**
($164.71 non-compound vs. $133.46 compound ending balance from $100,
every one of the 4 streams individually worse compounded, Volume Raider
hit hardest -- its return nearly halved). Likely cause: volatility
drag/sequence-of-returns effect -- a losing stretch shrinks the balance
right before a stream's big winning trades, so those winners compound off
a smaller base than the fixed lot size would have given them.

**User does not trust this estimate at face value** (fair, given tonight's
track record) and wants it properly built and tested for real next
session -- true compounding support added to the single/staggered/
scale_up engine paths (not just a post-hoc reconstruction), then re-run
against Model 2's real composition.

**Also queued as an alternative, if compounding turns out not to help**:
a previously-tested idea (referenced by the user, not detailed in this
file -- ask for specifics next session if not otherwise documented) --
route realized gains into a separate accumulating "bucket" instead of
either compounding or letting them sit idle, held in something like a
buy-and-hold pattern, only sold/redeployed once that bucket grows by some
threshold (user mentioned 50% as the number tried before). Real
alternative to compounding for the "gains aren't doing anything right now"
problem -- worth a real test alongside the compounding build.

---

## Superseded by the above — kept for history

# Handoff — 2026-08-05

## 🔴 (superseded) backtest + live-replay both fixed and merged to `main`. Next: a NEW branch (off `main`) to redesign Model 3/4 and see if either can actually be made good. `live-model-3` deployment is deliberately deferred until after that.

### The one-sentence version

This session found that the backtest had been crediting phantom fills (sales at prices the market never touched) for the entire life of the project — catastrophically for blended mode (Model 3/4, 10-20x inflation), modestly for Model 1/2 (10-25%, normal slop) — and that live code's exits used a market sell that could genuinely sell below the "never lose" floor. Both are now fixed, cross-validated against each other with a new live-replay harness, and merged to `main`. With the fabrication gone, Model 3/4's *honest* backtested performance is weak (Grade 2-3, not the Grade 4-5 story everyone believed) — **so the next session's job is a redesign attempt, on a fresh branch, before touching real money again.** Porting the fix to `live-model-3` (where real money sits right now, unprotected) is explicitly deferred until after that — see "Deferred, not forgotten" below.

### What to actually do this session

1. **Branch off `main`** (not off `live-model-4` — that branch is now stale, see below) for the Model 3/4 redesign work.
2. Use the backtest for fast iteration on stream parameters (arm threshold, ladder spacing/depth, capital weighting, maybe a different core signal entirely) — it's honest now, no longer fabricating fills, so real signal will show up in it.
3. **Before trusting any promising candidate, run it through `tools/live_replay/replay_gauntlet.py` too** (exact commands below) — backtest alone is not sufficient evidence, per the trust discussion below. A large backtest-vs-live-replay disagreement on a candidate is a stop sign.
4. Goal: find a configuration for Model 3 and/or Model 4 that clears something like Grade 4 (beats S&P by a real margin) *honestly*, not just clears break-even.
5. `live-model-3`'s real deployment gap (it's still running the old market-sell exit code, unprotected) is a separate, still-urgent item — but the user has explicitly chosen to defer it until after this redesign attempt. Don't start that work unprompted this session.

### What actually happened, in order

1. Built `tools/live_replay/replay_gauntlet.py` — drives the real production order-management code (not a backtest reimplementation) tick-by-tick against real historical data, with a safety pattern (both notifier `_dispatch` functions mocked, verified before anything else runs) that took two real alert incidents to get right. Found a single-trade discrepancy; fixed the harness's tick ordering to match production exactly; **the discrepancy persisted**, proving it was real, not a harness artifact.
2. Traced it to a real 5-slot GS: Reflex trade and found **two stacked bugs**: (a) `place_exit()` did an unconditional **market sell** the instant the floor was computed, which can fill far below the floor during an active crash — exactly when a position tends to arm for the first time; (b) the **backtest's own exit-fill check was one-sided** (`low <= effective_stop`, no `high` check, unlike every entry/add fill in the same file) — it could credit a sale at a price the candle's high never reached. **Bug (b) exists in all four backtest slot modes**, meaning every model's backtest carried this optimism, not just blended's.
3. Ran the full Primary v2 window (2022→present): backtest showed 182 trades / 2.2% loss rate / $100→$871; live replay showed 51 trades / **49% loss rate** / capital **permanently frozen at $49.42 by Aug 2022** (below the $10 minimum lot, never recovers). Loss rate scaled with slot count: 0% at 1 slot, **100% at 5 slots**.
4. **Fixed, for blended mode (Model 3/4, shared `blended_order_manager.py`/`blended_position_monitor.py`)**: exits are now a real resting **limit** sell at the floor (`ensure_pending_exit`/`check_pending_exit`, migration v8), re-priced as the floor moves, confirmed via a real poll — not an immediate market sell. `trail_armed` persists once true and permanently disables capitulation (a position that's proven it can arm should never be forced into the backstop meant for positions that never did) — but cascade adds keep working after arming, since a new cheaper fill only ever lowers the floor. Model 1/2 deliberately **kept** their market-sell exit (a real stop-loss should guarantee execution, not rest unfilled) — only got the backtest-side realism fix (`min(stop_price, close)`, "never assume a fill better than the close").
5. **Re-validated after the fix**: GS: Reflex's live loss rate dropped from 49% to **9%** (matching backtest), Grid Stacker Blended dropped to **0%**. Neither model's capital freezes anymore. Model 1/2 re-audited and confirmed only modestly affected (10-25% overstatement, not broken) — corrected backtest results saved permanently (`backtest.model_tests` id 127-136).
6. **Found a real double-sell safety bug while digging into why backtest and live-replay still didn't fully agree**: `ensure_pending_exit()` cancelled and replaced an existing resting order without ever checking if it had already filled for real first — in production (continuous real time, unlike backtest's discrete ticks), this could place a second sell order for BTC no longer held. Fixed: check real status first, finalize from that fill if already filled.
7. **Found and fixed a second real backtest bug**: every slot mode's loop checked for a fill on a pre-existing pending order *before* placing any new one each iteration — meaning an order placed this tick could never be checked for a fill until the *next* tick, even though its price (always the current close) trivially sits inside that same candle's own range and would fill immediately in reality. Fixed across all four slot modes. Verified safe for Model 1/2 (trade counts moved by at most 1, returns shifted under 1pp).
8. **The aggregate P&L gap between backtest and live-replay for blended mode never fully closed**, despite three rounds of real, verified fixes (each one individually confirmed correct via concrete trace evidence). GS: Reflex went from 2.7x to ~5.6x after the entry-timing fix — i.e., closing one real bug didn't monotonically shrink the gap. **Working conclusion: this is not a single remaining bug** — backtest and live-replay are two independently-built simulations of the same rules, and small mechanical differences between them compound over hundreds of trades into large aggregate differences. Not resolved; probably not worth chasing to zero. Loss *rate* and capital-freeze behavior are now honest and match well; exact cumulative P&L between the two systems does not, and shouldn't be trusted to the dollar from either side alone.
9. **Merged to `main`** (cherry-picked cleanly off the `live-replay-testing` branch, deliberately excluding that branch's `live-model-4`-specific workflow renames and Model-1-only dead-code deletions — those stay specific to that branch, since Model 1 may still dispatch against `ref:main` for `executor.yml`/`healthcheck.yml`). 40/41 tests passing (`test_signal_parity.py` excluded, pre-existing unrelated issue).

### What this means for decisions already in motion

**The documented reason for retiring Model 1/2 ("Model 3/4 beat them 3-7x") no longer holds.** With honest numbers on both sides: Model 1 ~9-24% annualized depending on preset, Model 2 ~11-17%, Model 3 ~-2-13%, Model 4 ~1-16%. Model 1/2 are comparable to or better than Model 3/4 now. **This retirement decision needs revisiting directly with the user — not yet resolved, flagged repeatedly this session, still open. Separate from the redesign work below — don't conflate the two.**

### Current honest numbers, all four models, all five presets (all saved permanently in `backtest.model_tests` — query by these IDs if you need the full row)

| Model | Preset | Trades | Ann. Return | Max DD | Total P&L | `model_test_id` |
|---|---|---|---|---|---|---|
| **1** (Momentum Rider + Dip Hunter + Breakout Scout, $33.33 each) | Full History | 143 | 15.2% | -19.0% | $236.48 | 128 |
| 1 | Primary v2 | 67 | 9.1% | -16.1% | $48.87 | 131 |
| 1 | Primary Window | 80 | 23.5% | -17.7% | $186.60 | 127 |
| 1 | Recent | 41 | 8.9% | -16.2% | $24.52 | 129 |
| 1 | 2026 YTD | 8 | 13.6% | -3.0% | $7.75 | 130 |
| **2** (Breakout Scout + Dip Hunter + Momentum Rider + Volume Raider, $25 each) | Full History | 219 | 12.6% | -23.1% | $177.16 | 133 |
| 2 | Primary v2 | 108 | 11.5% | -16.3% | $64.71 | 136 |
| 2 | Primary Window | 120 | 17.5% | -19.5% | $123.71 | 132 |
| 2 | Recent | 66 | 12.9% | -15.8% | $36.92 | 134 |
| 2 | 2026 YTD | 13 | 11.6% | -3.3% | $6.62 | 135 |
| **3** (Grid Stacker Blended v8, $100 solo) | Full History | 96 | 2.5% | -43.5% | $23.71 | 143 |
| 3 | Primary v2 | 68 | 1.5% | -43.5% | $7.00 | 146 |
| 3 | Primary Window | 72 | 12.6% | -32.7% | $80.90 | 142 |
| 3 | Recent | 60 | **-1.8%** | -43.5% | -$4.56 | 144 |
| 3 | 2026 YTD | 2 | -43.9% | -28.8% | -$28.72 | 145 |
| **4** (GS: Reflex v2, $100 solo) | Full History | 70 | 1.3% | -47.2% | $11.56 | *(not saved — see note)* |
| 4 | Primary v2 | 45 | 3.5% | -47.2% | $17.23 | *(not saved)* |
| 4 | Primary Window | 70 | 11.9% | -33.1% | $75.04 | *(not saved)* |
| 4 | Recent | 37 | 0.5% | -47.2% | $1.36 | *(not saved)* |
| 4 | 2026 YTD | 2 | -43.3% | -28.7% | -$28.30 | *(not saved)* |

**Model 4's numbers are NOT saved to `backtest.model_tests`** — `backtest.model_streams` currently has **two conflicting rows** for model_version=4 (GS: Reflex `v1` and `v2` both attached), explicitly marked in its own description as "not finalized/deployed; placeholder." Running the official `run_model()`/`save_model_test()` path against it right now would double-count both stream configs into one result. **Fix this (delete the stale `v1` row, keep only `v2`) before trying to save Model 4 results the official way** — the numbers above came from a direct `run_backtest()` call bypassing that layer, which is fine for reference but shouldn't be treated as saved/permanent. The 2026 YTD numbers for both models 3 and 4 (~-43%) are a 2-trade sample in a single bad stretch — noise, not signal, don't over-read it.

**⚠️ Stream Tester (individual stream-level view) is stale for all six relevant streams — checked directly, deliberately left as-is, not a bug to fix.** Grid Stacker Blended, GS: Reflex, Momentum Rider, Dip Hunter, Breakout Scout, Volume Raider all have `backtest.stream_tests` rows that predate this session's engine.py fixes (some from hours before, some from days/weeks before) — GS: Reflex's latest saved row shows `total_pnl` up to $31,486 on a $100 stream, the old phantom-fill-inflated math. If you open Stream Tester and see numbers like that, it's stale data, not a regression — expected to get naturally overwritten as the redesign work re-runs these streams anyway. User's explicit call: leave it, don't spend time re-running these now.

### What to do with the `live-model-4` branch

**Keep it, but treat it as stale — do not build the redesign work on top of it.** It's still at commit `e6124e3`, unchanged since before this session, meaning it still has the market-sell exit bug, the phantom-fill backtest bug, and none of this session's fixes. It has real, still-valid infra prep on it (the `executor_m3.yml`→`executor_m4.yml` workflow rename, `LIVE_MODEL_VERSION=4`, deletion of genuinely Model-1-only dead code) for the eventual plan of repurposing Model 1's infra slot for Model 4 — that plan itself is still fine, just premature until a redesigned Model 4 config is actually validated. When that day comes: either rebase `live-model-4` onto current `main` (bringing in all of this session's fixes) or re-apply just its infra-specific diff (workflow renames, dead-code deletion) onto a fresh branch cut from `main` at that time — don't just pick up where `live-model-4` left off assuming it's current, it isn't.

### How to use the two live-replay tools (both under `tools/live_replay/`)

**Blended mode (Model 3/Grid Stacker Blended, Model 4/GS: Reflex, or any future blended-mode stream)** — `replay_gauntlet.py`:
```
python -m tools.live_replay.replay_gauntlet --stream-config-id <id> --version <v> \
    --start 2022-01-01 --end 2026-08-05 --lot-size <per-slot $> --slot-count <n> \
    --slot-mode blended --stream-name "<name>"
```
e.g. Model 3: `--stream-config-id 11 --version v8 --lot-size 20 --slot-count 5`. Model 4: `--stream-config-id 12 --version v2` (same lot/slot). Takes ~40-90s for a 2022→present run. Prints a `BACKTEST reference` block, then ticks through, then `LIVE REPLAY:` with every closed position (slots used, entry avg, exit, pnl, reason).

**Plain single-slot mode (Model 1/2 streams)** — `replay_model1.py`, same idea, no `--slot-count`/`--slot-mode`:
```
python -m tools.live_replay.replay_model1 --stream-id <id> --version <v> \
    --start 2022-01-01 --end 2026-08-05 --lot-size <$> --stream-name "<name>"
```
Stream IDs: Momentum Rider=1, Dip Hunter=2, Breakout Scout=3, Volume Raider=4.

**Both scripts print a notifier-mock safety check first** (`Notifier mock check ... must print True for both`) — if you ever see `False` there, or the script doesn't print that block at all before doing anything else, stop and do not let it continue; that check existing and passing is what makes it safe to run against real (non-dry-run) order-placement code. Two real alerts fired before this pattern existed — don't simplify it away, don't skip it "just this once."

**Do not run these in parallel against the same local Postgres** — they share a reserved sentinel `model_version` (991 for blended, 992 for Model 1) for their sandbox rows; running two at once causes a real collision (confirmed this session — corrupted both runs). Run them sequentially.

### How much to trust backtest vs. live-replay going forward (asked directly this session, worth restating)

Backtest: fast, no longer catastrophically wrong, good for first-pass iteration and comparing variants quickly. Not precise enough to trust for an exact number — still diverges from live-replay by a wide margin on blended mode's cumulative P&L (though loss *rate* now matches well).
Live-replay: exercises the *real* production code, which is why it caught every bug above that a backtest reimplementation structurally never could. But it's still a simulation on the same historical OHLC data with the same touch-based fill assumptions (no order-book depth, no real slippage beyond candle-close for market orders) — it is **not** independently verified against actual Kraken execution, and its absolute numbers shouldn't be treated as ground truth either.
**Practical rule:** backtest for fast iteration; anything before a real deploy decision needs to also clear live-replay, and a large disagreement between the two is a stop sign, not a detail to shrug off. Neither one alone is sufficient for a go/no-go call. The only real ground truth is actual executed trades on Kraken, which barely exist yet (Model 3 has fills but no completed real trailing-stop exit cycle).

### Deferred, not forgotten (do not start unprompted — user's explicit call)

1. **Port the blended live-code fix to `live-model-3`.** Real money, real open position, no protection (still on the old market-sell exit) until this ships — genuinely the single most urgent item in the project, but explicitly deferred until after the Model 3/4 redesign attempt above. Needs: migration v8 applied to Supabase (not just local Postgres), the `blended_order_manager.py`/`blended_position_monitor.py`/`blended_executor.py` changes merged in, full `tests/live/` green on that branch, then a careful, deliberate deploy (review the diff against `live-model-3`'s current state first — it diverged from `main` before this session started).
2. **Revisit the Model 1/2 retirement decision** with the user, using the corrected numbers above.
3. Consider whether to formalize the live-replay harness as a named, mandatory process (parallel to "The Gauntlet") every model must pass before deployment.

**⚠️ Safety pattern, still required for any future replay work, unchanged:**
`mock.patch("src.live.blended_notifier._dispatch")` and `mock.patch("src.live.notifier._dispatch")` wrapping the entire script, with a printed+asserted call-through check *before* anything else runs. `replay_gauntlet.py`'s `_verify_alerts_mocked()` does this first thing in `run_replay()`. Two real alerts fired before this pattern was adopted — do not simplify it away.

---

## ⏸️ PAUSED (superseded by the above — kept for history): Model 4 (GS: Reflex) replacing Model 1 on the same infra

**Decision (user's, explicit, documented):** Models 1 and 2 are being retired ahead of schedule — not for underperformance in the sense the Model Commitment Rule was written to guard against, but because Models 3 and 4 are so far ahead (Model 3 ~3x, Model 4 ~4.5-7x Model 1/2's annualized returns, verified against real numbers, holds up after realistic tax treatment too) that continuing to run 1/2 isn't worth it. This is a one-time, conscious exception to the Commitment Rule — CLAUDE.md's Model Tournament section and the Commitment Rule subsection currently contradict each other slightly (one says "manually stopped" is a valid end condition, the other doesn't) — **CLAUDE.md still needs updating to reflect this decision and resolve that contradiction. Not done yet.**

**The plan:** repurpose `live-model-1`'s branch/GitHub Actions/cron-job.org slot for Model 4, reusing the existing $100 capital (once Model 1's Breakout Scout position is manually sold) rather than deploying new money. Model 3 stays running unchanged.

**Where things stand right now:** the code side of this is substantially built (see below) but **nothing has gone live yet** — no cron-job.org changes, no Supabase changes, no real position closed. Everything so far is safe, local/GitHub-only work.

## Done This Session

**1. Clean Model 4 cutover to `main` (separate from the live-infra work, landed first):**
- PR #6 merged: `src/backtester/engine.py`'s `_run_blended_slots` now carries only what's actually used — Model 3 (v8, live)'s `capitulation_stop_pct`, plus Model 4 (GS: Reflex v2)'s capitulation ladder, sentiment tilt, slot promotion, shallow breakeven margin. Every rejected/unexercised experiment from the design session (concurrent stacks, skim bucket, wide-trail, vol-adaptive, `prior_slot` promotion anchor, ladder start/step shorthand) removed.
- `docs/decisions/007-model4-gs-reflex-design.md` — renamed from `model4-design-notes.md` to follow the existing ADR numbering convention; now documents what shipped vs. what stayed archived.
- Verified via independent hand-traced math (matched real engine to full float precision) and full preset re-runs (byte-identical to pre-cutover numbers) before and after.
- Also on `main` this session (separate, earlier work, already shipped): Stream Tester performance fix (PR #5) — payload bloat (raw OHLCV dataframe in every saved pkl) and `st.tabs` eagerly rendering every tab on every rerun, both fixed.

**2. `market_data.yml` moved to its own `live-market-data` branch.** It was hardcoded to check out `ref: live-model-1` — an accident of history, and exactly the kind of fragility that caused a real incident already (documented 2026-08-02 entry below). Now insulated from main's churn the same way live-model-1/3 already are. **cron-job.org's market_data job still needs to be retargeted to `ref:live-market-data` — not done yet, this is a required step before touching live-model-1/4.**

**3. `live-model-1` → `live-model-4`.** Old `live-model-1` branch left untouched on GitHub as a rollback point (not deleted). New `live-model-4` branched fresh from `main` (not from old `live-model-1` — tried that first, caught by the user as messier than necessary; also confirmed `main` already had every blended file live-model-3 does, with equal-or-better comments, so `main` was the right base, not `live-model-3` either). On this branch:
- **Caught and fixed my own mistake before pushing:** a broken grep filter hid the fact that `blended_executor.py`/`blended_order_manager.py`/`blended_position_monitor.py` genuinely import shared helper functions and fee constants from the plain `executor.py`/`notifier.py`/`order_manager.py`/`position_monitor.py`. I initially deleted all of these as "dead Model-1-only files," then restored all four after actually trying to import the modules in Python (not just grepping) and hitting `ImportError`. Only `deploy.py`, `healthcheck.py`, `setup_supabase.py`, `market_data_updater.py` are genuinely Model-1-only dead code — confirmed removed safely.
- Added `executor_m4.yml`/`healthcheck_m4.yml` (renamed from `_m3` versions — `ref: live-model-4`, `DRY_RUN_M4`). Old `executor.yml`/`healthcheck.yml`/`executor_m3.yml`/`healthcheck_m3.yml` removed from this branch.
- Bumped `LIVE_MODEL_VERSION` to 4 in `blended_executor.py`/`blended_healthcheck.py`, updated all Model-3-specific log/error text. Diffed line-by-line against `live-model-3`'s originals to confirm every change was exactly the intended one, nothing accidental.

**4. Critical gap found and fixed: none of Model 4's four mechanisms existed in the live code.** They were built into the backtest engine only. Deployed as-is, Model 4 would have silently traded as plain Model 3 forever — the new config keys would just go unread. Fixed:
- `_tilted_slot_weights` extracted from `engine.py` into `src/backtester/slot_math.py` (`tilted_slot_weights`, pandas-free) alongside `slot_capitals_for`, so backtest and live share the literal same formula. Confirmed behavior-preserving.
- `blended_order_manager.py`: `place_entry()` now reads today's real Fear & Greed value (new `_get_fng_value`, queries `sentiment_data` directly) and freezes the tilted split into a new `frozen_slot_capitals` column so cascade adds don't re-tilt against a later reading. `check_cascade_add_trigger()` gained the `slot_promotion_days` "impatience" trigger, reproduced with its exact real quirk (promotion allowance spent whether or not it fires that candle) rather than an idealized version.
- `blended_position_monitor.py`: `check_all()` gained the capitulation ladder (new `marked_count`/`marked_capitals` columns) and the shallow breakeven margin.
- `src/data/migration_v7_model4_mechanics.sql` — adds `frozen_slot_capitals`, `promotions_used`, `marked_count`, `marked_capitals` to `live.blended_positions`. **Applied to local Postgres only. Supabase still needs this migration run before any real deploy — not done yet.**
- New `tests/live/test_model4_mechanics.py` (5 tests, all passing) — real integration coverage against a mocked Kraken client + local Postgres for all four mechanisms, where there had been zero. Two of the five initial test scenarios failed on first write — not because the code was wrong, but because of a test arithmetic error and a wrong assumption about ladder timing (confirmed the second one was correct, Gauntlet-validated behavior by reproducing it directly in the backtest engine before adjusting the test).
- New `tests/live/test_blended_executor_model4_lookup.py` — the one thing actually changed (`LIVE_MODEL_VERSION`) had zero test coverage until this was added.
- Full suite: **38/38 passing** (pre-existing unrelated `test_signal_parity.py` collection error excluded, as always).
- Backtest/Gauntlet re-confirmed byte-identical on `live-model-4` multiple times throughout, most recently just before this handoff was written.

**5. ⚠️ Two real safety incidents this session, both from ad-hoc verification scripts (not from anything in the committed codebase) — read before running anything like this again:**
- A standalone Python replay script (not a pytest test) drove real (non-dry-run) order-placement code without blocking alerts, and a real email/SMS fired. Root cause: blanking `ALERT_*` env vars via `os.environ.pop()` is **fragile** — any later `load_dotenv()` call (several `src/live/*` modules call it) silently refills a var that's merely *missing* (dotenv's `override=False` default only protects vars that are still *present*). `tests/live/conftest.py`'s fixture works because pytest controls timing relative to imports; a standalone script does not have that guarantee.
- **The fix that actually works, verified in isolation before trusting it:** `mock.patch("src.live.blended_notifier._dispatch")` and `mock.patch("src.live.notifier._dispatch")` — both real chokepoints every alert call funnels through, immune to `load_dotenv()`. Any future script that drives real (non-dry-run) `blended_order_manager`/`blended_position_monitor` code **must** wrap the entire execution in both patches, and should print+assert a quick call-through check before doing anything else, exactly like `tools/live_replay_wip/live_replay_gauntlet.py` and `trade2_diagnose.py` now do (safely preserved on disk, untracked — see the "START HERE" section at the top of this document).

## Critical Open Item — live-code replay surfaced a real, not-yet-resolved question

Built a harness that drives the actual production functions (not the backtest) tick-by-tick against real historical `market_data`/`sentiment_data` in a local Postgres sandbox, with a fake Kraken client. This is fundamentally different from the backtest — it exercises the real DB-backed state machine and, critically, simulates real market-order slippage on exits (the backtest always assumes a perfect fill at the exact stop price; a real market sell fills at whatever the market offers).

Replaying Jan 2026: entry/exit **dates** matched the backtest exactly, but Trade 2's **P&L flipped from a small backtest gain (+$1.01) to a real loss (-$7.18)**. Root-caused with real per-tick instrumentation, not guessed: the position sat at 4 filled slots with `armed=False` for over a week while price crashed, because `gain_pct` (measured against a stale high-water-mark) stayed just under the 4% arm threshold. The **5th and final slot filled right at the bottom of the crash**, which *itself* dragged the average cost down enough to clear the arm threshold *for the first time* — and the instant it armed, that same candle's low was already below the newly-computed breakeven stop, triggering an immediate market-sell exit that filled at the crashed price, not the stop level.

**Not yet resolved:** whether this is a real, previously-unknown risk in the design (a last-slot fill during a crash can arm and immediately force-exit at a bad price in the same instant) or partly an artifact of the replay harness's tick ordering, which was built to match the *backtest's* loop order rather than production's *real* `tick()` order (production checks for a new cascade-add trigger and places that order *before* polling for fills in the same tick — the harness does it in the reverse order). This needs the harness rebuilt to match production's real ordering exactly, then re-run, before drawing a final conclusion. User wants this "tested hard" — likely its own dedicated session given the safety incidents above.

## Remaining Steps To Go Live (in rough order)

1. **Resolve the live-replay timing question** — see the "START HERE" section at the very top of this document; this is now its own initiative on its own branch, paused separately from the rest of this list (or explicitly decide to proceed without full resolution, if that's the call once it's investigated).
2. Update CLAUDE.md's Model Tournament/Commitment Rule contradiction + document the Model 1/2 retirement decision.
3. Write `deploy_model4.py` (adapted from `deploy_model3.py`) — **capital must be seeded from whatever real dollar amount Model 1's sellout actually returns, not a hardcoded $100** (this is a capital carry-forward, not new money).
4. Adapt the Model-1-vs-Model-4 isolation test (mirror `tests/live/test_blended_isolation.py`, which currently only proves Model-1-vs-Model-3 isolation).
5. Port the pure `slot_math.py`/`engine.py` refactor (item 4 above) back to `main` via its own small PR — it's currently only on `live-model-4`, and `main` should have it too since it's a verified no-op refactor.
6. **User's turn:** pause Model 1's cron-job.org jobs, then manually sell Breakout Scout on Kraken. Tell Claude the real settlement amount for `deploy_model4.py`.
7. **User's turn:** on cron-job.org — retarget the executor/healthcheck jobs to `executor_m4.yml`/`healthcheck_m4.yml` + `ref:live-model-4`; retarget the market_data job to `ref:live-market-data`. Apply `migration_v7_model4_mechanics.sql` to Supabase. Add a `DRY_RUN_M4` GitHub secret (start `true`).
8. Manually trigger one dry-run dispatch, confirm clean, then flip `DRY_RUN_M4` to `false`.



## ✅ Real fees were wrong for every model — now corrected everywhere, including the live branches

Kraken's real current fee tier (confirmed live via the `TradeVolume` API, re-checked multiple times, not assumed): **maker 0.40%, taker 0.80%** — double what every backtest and live formula assumed (0.25%/0.40%) since the project started. Fixed everywhere: the constants, the backtest engine's round-trip math (was double-maker, now correctly maker-entry/taker-exit), a fee-drift safeguard that checks Kraken's real tier every healthcheck cycle and alerts on mismatch, fees threaded as an explicit re-runnable parameter instead of hardcoded, and a full cleanup pass that replaced every stale/duplicated backtest row for the 3 currently-deployed model compositions in place (no new rows, legacy data left untouched). Deployed to `main`, `live-model-1`, and `live-model-3`. QA'd at every stage — see the 2026-08-03 "Done This Session" entries below for the full story.

**Update, same day, later session: the deeper gap flagged above (live code never captured Kraken's real per-trade fee/fill price) is now fixed too — see "Real per-trade fee capture" entry at the top of "Done This Session" below.** That work also found and fixed a real gap in this section's fee-drift safeguard: it only ever ran from Model 1's healthcheck, never Model 3's.

## Current State

**Model 1 is LIVE** — executor running, cron on schedule. Full alert coverage active (order placed, filled, closed, expired, system down).

**Model 2 is assembled and backtested.** Run 3 selected as deployment config. Not yet deployed — deprioritized behind Model 3.

**Model 3 is LIVE — trading for real, as of 2026-08-02 ~00:35 UTC.** "Grid Stacker Blended", $100 real capital in Kraken. `DRY_RUN_M3=false`. Real Kraken connectivity confirmed (`Kraken connected — USD: $166.54 BTC: 0.00053149` — whole-account balance, shared with Model 1; Model 3 sizes off its own `live.blended_capital` ledger, never this figure). Cron-job.org's two Model 3 jobs (`executor_m3.yml` every 30min, `healthcheck_m3.yml` every 2h) both confirmed firing successfully, dispatching with `ref:live-model-3` (switched from `ref:main` after the incident below made clear why that matters). `live-model-3` branch is fast-forwarded to match `main` exactly as of commit `3a11cf6`.

**Explicit user decision, not the original plan:** the original plan called for a multi-day dry-run logging trial before going live. User explicitly chose to skip it ("I don't mind a little turbulence... would rather have a good order happen for real than miss out because it's a dry-run") — flagged clearly before flipping the switch that real order placement had zero live Kraken mileage (only ever tested against a mock), user accepted that knowingly.

**Update: first real trade fired and confirmed legitimate.** Slot 1 filled 2026-08-03 04:01:06 UTC at $62,822.12 ($20 deployed), on a real ~1.11% 4h dip (candle closed at $62,793.40 vs. the prior candle's $63,500.00 close — clears the `fear_dip` 1% threshold; no other filters/sentiment gate on this config). Independently re-verified by running a from-scratch backtest starting at Model 3's actual go-live moment (2026-08-02) through the present — it reproduces the exact same single trade, same entry candle, same still-open/unarmed status as the real live position. Not a fluke. Position is currently OPEN, 1/5 slots filled, unarmed (needs +4% gain above avg cost to arm the trailing stop — see "Done This Session" below for the full mechanics writeup).

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

## Done This Session (2026-08-03, latest of all) — Stream Tester capital-basis bug, Model 3 fee-retune sweep (no upside found), a real capitulation loss discovered in Full History — **Model 4 discussion queued for next session**

**Stream Tester was still using the generic $20 default for blended-mode runs.** The one-off script fix from earlier today (`cleanup_and_rerun_fees.py`, since deleted) never made it into the interactive app itself — running "Model 3 Live Sync" from the UI silently used $20 instead of Grid Stacker Blended's real $100 pool. Added `load_stream_test_capital()` to `src/app/db.py` (derives the real pool size from `backtest.model_streams` for blended configs), wired into all three run paths in `stream_tester.py`. Re-ran and replaced the one affected row (`test_id=201`) in place. Also added a per-preset "↺ Re-run this preset" button — previously the only re-run path once a chart existed was the page-level "Re-run All," which reruns every preset for the config, not just one. Committed `3b72788`, `f65a0d2`. Both required a Streamlit server restart to pick up (module already loaded in the running process — same class of stale-server issue as an earlier session).

**User asked whether Model 3's parameters should be re-tuned now that real fees are known** (0.80% taker vs. the 0.25%/0.40% originally assumed). Decided **against** touching the live deployment (discussed and explicitly agreed: Model Commitment Rule exists precisely to prevent "I don't like this, let's redo it" reasoning, even when the underlying defect was arguably "our fault" — same reasoning applies whether the bad outcome already happened or hasn't happened yet). Instead ran a backtest-only sweep of `trail_arm_gain_pct` and `cumulative_drop_pcts` spacing as a **Model 4 candidate exploration**, not a live change:

- Confirmed the currently-deployed v8 config already fully reflects real fees (re-running it reproduces the DB exactly: +59.0%/+34.6% ann on Full History/Primary v2). Even a worst-case stress test (every entry forced to pay taker instead of maker, simulating the spread-crossing risk seen on the real first trade) only degrades it to +55.4%/+32.2% — the design was never resting on an inflated fee assumption.
- Swept `arm_gain` (5/6/7) and wider cascade spacing, alone and combined. **Most of the exciting-looking results (e.g. arm=6 + drops=[2,4,8,15] → +87.5% ann, 97% win rate, ~0% drawdown) are not trustworthy** — `max_drawdown_pct` (`metrics.py`) only sums realized (closed-trade) P&L, and the blended engine's own design "never voluntarily realizes a loss" until forced. Widening the spacing just delays cascade-adds/closes further, and since most of the dataset is a BTC bull run, positions that stay open longer tend to eventually recover instead of ever being marked as a loss — hiding real capital-at-risk, not reducing it. Raising `arm_gain` *alone* (no spacing change) shows the honest version: real drawdowns balloon to 44-69%.
- Only one variant survived scrutiny — `cumulative_drop_pcts = [1,3,6,12]` (vs. current `[1,2,5,10]`) — and it's a wash (+59.2%/+39.7% vs. +59.0%/+34.6%, same drawdown, fewer trades). **Conclusion: no v9 candidate worth carrying forward from this sweep.** Before trusting any future sweep like this, the metrics engine needs a real mark-to-market drawdown calc (peak-to-trough while a position is open, not just realized P&L) — flagged, not built.

**While comparing Model 1/2/3 head-to-head** (all re-run with the same real fees, confirming Model 3 is still the strongest by a wide margin — +59.0%/+34.6% ann vs. Model 1's +17.7%/+11.4% and Model 2's +16.6%/+14.1%, for only modestly worse drawdown), **found a real capitulation-stop loss in Model 3's Full History backtest that hadn't been individually surfaced before**: 2025-10-29 → 2025-11-21, slot 5 (all slots filled, out of ammo), realized loss of **-$1,282.64** — the sole source of the -21.18% max drawdown (every other "losing" trade in the 481-trade history is floating-point noise, effectively $0, the breakeven floor working as designed). **Recovery has been slow and incomplete**: equity peaked at $6,056 (2025-10-17), dropped to $4,773, and had only climbed back to $5,363 by the end of the dataset (2026-08-03) — still ~11% below the pre-loss peak, ~9 months later.

**User's reaction: this is a legitimate design weakness worth addressing, and wants to discuss it next session as part of Model 4 planning** ("I have some interesting ideas"). Explicitly **not** a live Model 3 patch — same reasoning as the fee-retune discussion holds even more cleanly here, since no live capitulation event has happened yet (there's no unfairness to correct, and patching preemptively would forfeit the chance to see whether live Model 3 handles a real capitulation event the way the backtest predicts — valuable given this project has already found several live/backtest divergences this week). **Queued for next session**: discuss Model 4 (or a "3.1"-labeled direct evolution of Model 3, still deployed with its own separate capital per the tournament rules — no earlier than Sep 1 per user's own stated timeline) with a specific focus on redesigning the capitulation-stop backstop so a crash-worse-than-history event doesn't cost as much or take as long to recover from.

---

## Done This Session (2026-08-03, even later) — Dashboard fixes for Model 3's first real trade, live-vs-backtest sync-check presets

Model 3's first real fill (see "Current State" above) surfaced several dashboard bugs while actually looking at it live for the first time.

**Live Monitor (`2_live_monitor.py`) Slot Status fixes:**
- Filled-slot line was rendering as raw HTML text (`</span><span style='...'>`) instead of styled output — Streamlit's `$...$` LaTeX-math interpretation ate everything between two unescaped dollar signs in the same markdown call, including the HTML tags. Same known gotcha this file was already patched for elsewhere (see the 2026-08-02 "fix LaTeX-dollar caption bug" entry) — missed it in new code, now escaped throughout (`\$` everywhere a markdown/caption call has a dollar sign).
- The one slot actually being watched right now (not every future slot) gets a live proximity bar toward its cascade trigger, matching Slot 1's pre-entry bar style.
- New **"Selling:"** line: shows an arm-progress bar while the trailing stop isn't armed yet (with the "a drop just buys more, doesn't sell" caveat spelled out), and once armed, the real breakeven-floored stop price and live distance to it.
- Removed the old unconditional "Trail stop $X (5% below HWM)" caption — it implied an active stop before the trail actually arms, which is exactly what confused the "how does this sell if we're already up" question below.
- Added glossary entries for Trail Arm %, Cascade Add, Capitulation Stop — the glossary only ever described Model 1's simpler always-on trailing stop.

**Model Dashboard (`3_model_dashboard.py`) fixes, same root causes:**
- "Capital Deployed" metric was truncating (`$20.00 of $10…`) — too wide for a quarter-width `st.metric`. Split into the metric (`$20.00`) plus a caption below (`of $100.00 pool`).
- Same arm-unaware "Trail Stop" bug as Live Monitor, in two places (the Slot Status current-position card and the Open Positions table). Now shows "Not armed — needs +N% (at +M%)" before arming, real breakeven-floored price after. Required adding `trail_arm_gain_pct` to `load_dashboard_lots()`'s queries in `db.py` (backtest + live-blended) — wasn't being loaded at all before.
- **Real bug found**: the Model selector was silently defaulting to **Model 2**, not Model 3 (`index=min(1, len-1)` picked list position 1, which happened to be "Model 2"). Fixed to explicitly default to "Model 3" by label match. Data-source toggle now defaults to Live instead of Backtest.

**Stream Tester** now defaults to the "Grid Stacker Blended" stream (the one checked most often) instead of whatever sorted first alphabetically.

**The "4% arm" mechanic, explained and verified against real numbers** (came up via user questions, worth recording): the trailing stop is NOT active until the position's HWM first rises `trail_arm_gain_pct` (4%) above the **blended average cost** — which shifts every time a new cascade slot fills, not the original Slot 1 price. Below that threshold, a price drop only triggers the next cascade buy, never a sale — nothing is watching for an exit at all. Once armed (permanently — HWM never decreases), the stop is `max(HWM × (1 − 5%), avg_cost / (1 − taker_fee))` — the breakeven floor wins immediately after arming, and the plain 5%-trailing calc only takes over once price has climbed further (~$66,657 for the current position, vs. arm at ~$65,335). Caveat for the record: the breakeven floor only backs out the exit-side fee, not the entry-side fee already paid, so "never sells at a loss" is very close but not literally penny-perfect.

**New "Live Sync" timeframe presets** (`timeframe_presets` table + `src/data/seed_presets.sql`, so they survive a DB rebuild) — open-ended windows starting at each model's real go-live date, meant to be re-run anytime to confirm the backtester still reproduces what the live account actually did:
- **Model 3 Live Sync** (2026-08-02 →): re-run confirmed it reproduces Model 3's real first trade exactly — same entry candle, same still-open/unarmed status.
- **Model 1 Live Sync** (2026-07-03 →): sanity-checked via CLI (not saved) — **all three Model 1 streams show 0 trades** since go-live, including Breakout Scout, which has a real open live position. Consistent with the user's own prior conclusion that that position was accidental/not a legitimate signal fire (already investigated in an earlier session, not reopened here) — but worth noting since it's the first real example of this preset catching a live/backtest divergence.
- `seed_presets.sql` also gained `Primary v2`, which existed in the DB already but was never added to this seed file (found while adding the two new ones).

**Also:** restarted the Streamlit dev server — it had been running continuously since **July 26** with no restart, which was very likely the cause of reported sluggishness/UI-stuck symptoms (confirmed the process itself uses trivial memory; broader system memory pressure was unrelated Chrome/VS Code processes with the same multi-week uptime, not this app).

**Deployed:** `main` only (`c90aade`) — this is all Streamlit app/dashboard code plus one DB seed-data change, not live executor code, so no cherry-pick to `live-model-1`/`live-model-3` needed. Both `timeframe_presets` rows already exist directly in local Postgres (via psql, not just the seed file).

---

## Done This Session (2026-08-03, latest) — Real per-trade fee capture (the deeper gap from earlier today) and a backtester/scripts cleanup pass

**The ask:** live code never captured Kraken's *real* per-trade fee or fill price — it only ever estimated P&L using the `MAKER_FEE`/`TAKER_FEE` constants. Worse than just an estimate-vs-real gap: `place_exit()` in both order managers was never even told Kraken's real fill price for a market sell — it used the *theoretical stop-trigger price* computed by the position monitor, not a confirmed fill. And Model 3 specifically never applied any entry-side fee at all, anywhere (not just on cascade adds — slot 1 too), by design (its own code comment admitted it).

**Fixed, all three gaps together (confirmed scope with the user first):**
- `kraken_client.get_order_status()` now surfaces Kraken's real `fee` field (quote-currency dollars) from both `QueryOrders` and the `TradesHistory` fallback branch — previously discarded entirely.
- Both order managers now poll `get_order_status()` **synchronously, once, right after placing the exit's market sell** (mirrors how entries already get confirmed) and use the real price + real fee for P&L. If that single poll doesn't confirm a fill yet (rare — API lag), falls back to the old estimate and sets a new `fee_is_estimated` flag rather than blocking the lot from closing.
- Model 3: every fill (slot 1 *and* every cascade add) now records its real fee in `live.blended_fills.fee_usd`, summed at exit alongside the real exit fee. Previously the exit formula only ever subtracted `TAKER_FEE` once — no entry-side fee anywhere. Practical impact so far: small, since only one real fill had happened in production before this fix (no exit yet), so nothing realized was corrupted — but the capital ledger would have quietly overstated `available_capital` by ~0.4% per entry fill from here on if left unfixed.
- New columns (additive, nullable, applied to **both** local Postgres and Supabase — `migration_v6_real_fees.sql`): `live.lots.entry_fee_usd`/`exit_fee_usd`/`fee_is_estimated`, `live.blended_fills.fee_usd`, `live.blended_positions.exit_fee_usd`/`fee_is_estimated`.

**QA bar was explicitly "thoroughly confident before touching production," and re-verification caught a real bug in the fix itself:** Model 3's `place_exit()` originally summed entry fees with `SELECT SUM(fee_usd)`. SQL's `SUM` silently skips `NULL`s — and Model 3's real live position has exactly one fill (slot 1) with `fee_usd = NULL` (it predates this migration). The moment a *new* cascade add fills after deploy, that position has a mix of one legacy (NULL) and one real fill; `SUM` would've silently dropped the legacy fill's fee from the total and, since the result wouldn't itself be `NULL`, `fee_is_estimated` would've stayed `False` — wrong on both counts, no flag raised. Rewrote to walk fills individually, estimating (`capital * MAKER_FEE`) only the specific fill missing real data and flagging estimated if *any* fill needed the fallback. Added dedicated tests for exactly this scenario, plus the mirror case for Model 1 (every currently-open Model 1 lot in production has `entry_fee_usd = NULL` right now — the very next trailing-stop exit hits this path for real). **32/32 `tests/live/` tests pass** (added 2 new files/cases beyond the pre-existing 30; one unrelated pre-existing collection error in `test_signal_parity.py` — stale `locked_test_id` column reference, confirmed present before this session, out of scope).

**User then asked to verify the *backtester* wasn't hit by the same bug (worried it might invalidate Model 3's whole design).** It wasn't: read `_run_blended_slots` in `src/backtester/engine.py` line-by-line — it already correctly reduces simulated BTC quantity by `(1 - maker_fee)` on **every** fill (slot 1 *and* every cascade add, not just the first), baking the entry fee into the average cost basis correctly, with the taker fee applied once at exit on the combined stack. Re-ran Model 3's Grid Stacker v8 config across all 5 presets anyway (deleted and replaced the existing `stream_tests`/`model_tests` rows in place, not additive) — **numbers came back byte-identical** (+75.1%/+59.0%/+31.1%/+13.6%/+34.6% ann., same trade counts). Confirms the backtested numbers Model 3 was greenlit on were never inflated by this gap — only the live execution code was drifting from that already-correct target, not the other way around. No model design changes needed.

**A real gap was also found and fixed in the fee-drift safeguard from earlier today: `check_fee_drift()` was only ever wired into `healthcheck.py` (Model 1's), never `blended_healthcheck.py` (Model 3's)**, on the assumption that one shared Kraken account meant one shared check sufficed. That's false: Model 1 and Model 3 live on **separate branches**, each keeping its **own independent copy** of `MAKER_FEE`/`TAKER_FEE` — those copies could drift from *each other*, not just from Kraken's real rate, and a check running from only one model's healthcheck would never catch that. Fixed: `check_fee_drift()` now runs from **both** `healthcheck.py` and `blended_healthcheck.py`, on `main`, `live-model-1`, and `live-model-3` — the old "check once" comments in `fee_check.py`/`healthcheck.py` were corrected to explain why both are needed.

*(Correction to an earlier draft of this entry: I initially believed, based on a stale local copy of the `live-model-3` branch that hadn't been re-fetched from GitHub, that the branch was still running the original wrong 0.25%/0.40% constants and was missing `get_fee_tier()`/`fee_check.py` entirely. That was wrong — the real branch on GitHub already had all of that correctly. Caught before anything false was pushed to that branch; only the genuine `blended_healthcheck.py` wiring gap above was real. Flagging here so the record's accurate, not because it changed what shipped.)*

**Also cleaned up 4 completed one-off backtester scripts** (`finalize_model3.py`, `backfill_model3_presets.py`, `rerun_corrected_fees.py`, `cleanup_and_rerun_fees.py`) at the user's request after noticing them during this work — all fully superseded, non-reusable (hardcoded IDs/compositions from one moment in time), and already reflected in the DB + this file's history. Deleted from `main`; the cherry-pick to `live-model-3` carried the one deletion relevant there (`finalize_model3.py`) along with it. Fixed a resulting stale comment in `deploy_model3.py` (`BASED_ON_MODEL_TEST_ID = 106` — that row no longer exists, replaced by a new id during the original 2026-08-03 cleanup and again by today's re-verification rerun; harmless dangling soft-reference, `live.models.based_on_model_test_id` isn't a real cross-DB FK).

**Deployed:** `main` (`5529d34`, `be1641f`, `f980eea`), `live-model-1` (`b2c26b8` — targeted cherry-pick, kept that branch's own local fee-constant copy, ported the new `_fake_kraken.py`/`conftest.py`/test file so this critical fix has real test coverage on that branch too), `live-model-3` (`b21e3cb` fee-capture cherry-pick, `ee495e6` fee-drift-healthcheck wiring — full 32/32 `tests/live/` pass on that branch). Supabase migration applied and confirmed against the two real currently-open positions (Model 1 `lot_id=2`, Model 3 `position_id=1` — both correctly `NULL` on the new legacy columns, `fee_is_estimated` defaults `false` until their next exit triggers the fallback path for real).

**Known remaining gap, not addressed this session:** the pre-trade breakeven floor in `blended_position_monitor.py` is necessarily still `TAKER_FEE`-constant-based (evaluated before the exit order exists, so there's no real fee to read yet) — left as-is deliberately, now with a comment explaining why a future reader shouldn't "fix" it to use real data.

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

### ✅ DONE (2026-08-03, later session) — Fee round-trip math was still wrong even after the constant fix; found, fixed, QA'd, deployed to all three branches

The constant fix (0.25%/0.40% → 0.40%/0.80%) from earlier tonight only corrected the *values* — it didn't check *which* value got applied where. User asked directly: "are you sure of what the fees are both ways?" That surfaced a second, bigger bug: **`src/backtester/engine.py` computed round-trip fees as `fee*2` with `fee` defaulting to `MAKER_FEE` — i.e. every slot mode (single, staggered, cascade, blended) assumed BOTH legs filled at the maker rate (0.80% round trip).** But this project's own design has market orders on exit, which always pay the taker rate — real round-trip cost is `MAKER_FEE + TAKER_FEE` (1.20%), not `MAKER_FEE*2`. Same bug hit the breakeven-floor math in cascade/blended modes: the floor was priced off the maker rate for the exit leg, so "never voluntarily realize a loss" was under-provisioned by the maker/taker delta (~0.4%) and could still lock in a small real loss.

**Re-confirmed fees live via `TradeVolume` API before touching anything** — still lowest tier, maker 0.40% / taker 0.80%, unchanged since yesterday's discovery.

**Fixed:** `engine.py` now uses `MAKER_FEE` for entry and `TAKER_FEE` for every exit-side calculation (pnl and breakeven floor), across all slot modes — matching `order_manager.py`'s and `blended_position_monitor.py`'s already-correct live formulas. Also fixed two display-only copies of the same wrong assumption: `dashboard.py`'s blended trade log (`sell_fee` was `MAKER_FEE`, now `TAKER_FEE`) and a stale 0.25%/0.40% mention in `stream_tester.py`'s glossary.

**QA:** re-ran Model 3's full-history backtest (594 trades) — zero `trailing_stop` exits realize a real loss now (worst is -3.6e-12, floating-point noise). Only the one designed `capitulation_stop` trade takes a real loss, as intended (that backstop is supposed to). `tests/live/` 27/27 still pass (unaffected — no hardcoded fee-derived values). `tests/live/test_signal_parity.py` has a pre-existing unrelated collection error (references a dropped `locked_test_id` column) — confirmed present before this session's changes too, not something introduced here, not fixed (out of scope).

**Deployed to all three branches** (`main` 6744d94, `live-model-1` 2e4cad9 — targeted port of the constant fix only, since that branch carries independent live-only commits, `live-model-3` fast-forwarded to `main`'s tip via `git push origin main:live-model-3`, confirmed on remote). Both live models now have real open positions running against the corrected breakeven math — watch the next tick on each.

#### 1. ✅ Fix `live-model-1` for the real fees — DONE, see above
#### 2. ✅ Fix `live-model-3` for the real fees — DONE, see above

#### 3. ✅ Build the fee-drift safeguard — DONE (2026-08-03, later still)
`KrakenClient.get_fee_tier()` (new) queries `TradeVolume` and returns real maker/taker rates as decimals. `src/live/fee_check.py` (new) compares those against `MAKER_FEE`/`TAKER_FEE` in `order_manager.py` and fires `notifier.alert_fee_drift()` (new — email+SMS) if they've diverged beyond float tolerance. Originally wired into `healthcheck.py` only, on the assumption that one shared Kraken account meant one shared check sufficed. Verified against the real account (maker 0.40%/taker 0.80%, matches code) and against a mocked mismatch (correctly detects and fires). Deployed to `main` (79ef633) and `live-model-1` (62ed917, adapted for that branch's `DATABASE_URL` env var convention); `live-model-3` fast-forwarded to `main`'s tip but at the time didn't run this check directly.

**⚠️ Correction (2026-08-03, latest session): that "one shared account, one check" assumption was wrong.** Model 1 and Model 3 live on separate branches, each with their OWN independent copy of `MAKER_FEE`/`TAKER_FEE` — those copies could drift from *each other*, not just from Kraken's real rate, and a check running from only one model's healthcheck would never catch that (see the real-fee-capture session entry above for the full story). Fixed: `check_fee_drift()` now also runs from `blended_healthcheck.py`, on `main`, `live-model-1`, and `live-model-3` alike. Comments in `fee_check.py`/`healthcheck.py` updated to explain why both are needed, not just one.

**Caution during testing:** an ad hoc drift-simulation script (outside pytest, so it skipped `tests/live/conftest.py`'s `_no_real_alerts` fixture) sent one real "Forge: Fee Tier Drift Detected" alert — false positive, safe to ignore, not a real drift.

#### 4. ✅ Clean up Stream Tester / Model Tester — DONE (2026-08-03, later still)

User asked directly whether fees were really "both ways" correct, which led to fixing the round-trip math (see the entry above this one) — and then asked how to fix backtesting broadly, flagging that fees will keep changing over time (should be a parameter, not hardcoded) and that the Model Dashboard was showing duplicated presets (old-fee and new-fee rows for the same model/timeframe, indistinguishable). Both were real, structural gaps:

**Fees were duplicated as hardcoded constants** — `engine.py` and `order_manager.py` each independently defined `MAKER_FEE`/`TAKER_FEE`, the same drift risk that caused the original bug, one level removed. `src/fees.py` (new) is now the single source of truth; both import from it. **`main` only** — deliberately not ported to `live-model-1`/`live-model-3` this pass, since those branches already have the correct hardcoded values from earlier today and don't need the refactor to function correctly; only worth porting if it becomes actively confusing to maintain two patterns.

**Fees are now a parameter, not hardcoded** — `maker_fee`/`taker_fee` threaded as explicit optional args through `run_backtest()` → `run_model_backtest()` → `run_model()`, defaulting to `src/fees.py`. A backtest can be re-run under a hypothetical rate without touching code.

**`save_model_test()` never deduped — always INSERTed.** `save_stream_test()` already upserted on `(stream_config_id, preset_id)`, but `save_model_test()` had no equivalent, so every re-run left a new row and the old one stuck around — exactly what produced the duplicated-presets confusion on the Model 3 dashboard. Fixed to upsert on `(model_id, run_number, preset_id)` / `(..., custom_start, custom_end)`, re-seeding `backtest.lots` on replace. Added `fee_maker_pct`/`fee_taker_pct` columns to both `backtest.stream_tests` and `backtest.model_tests` (`migration_v5_fee_columns.sql`) — every row is now self-documenting; `NULL` means legacy (saved before fee-tracking existed). Stream Tester, Model Tester, and Model Dashboard all now show the fee rate next to each result and flag legacy rows.

**Cleanup pass** (`src/backtester/cleanup_and_rerun_fees.py`, one-time script): deleted every preset-based row for the 8 stream_configs and 3 model compositions currently locked/deployed (Model 1 live, Model 2 Run 3 selected, Model 3 live) — 35 `stream_tests` + 34 `model_tests` rows, some of which were true duplicates/triplicates from repeated re-runs — and re-ran fresh with the corrected fee math, one clean row per preset. Model 3 Full History: +71.7% (still-buggy symmetric fee math from earlier today) → **+59.0%** (fully correct maker-entry/taker-exit math). Verified via direct query: zero duplicate `(config, preset)` or `(model, run_number, preset)` rows remain in the bounded set, lot counts match trade counts exactly, all fee columns populated.

**Deliberately untouched, per explicit instruction ("legacy can be left alone")**: every other `stream_config` (VR v2/v3/v4, Quiet Climber, Cascade DCA, SMA Pullback, etc.), Model 1/2's non-selected `run_number`s (their old configurations, superseded), and all 56 custom-date "regime robustness" rows on Model 2. These still have `NULL` fee columns and show the legacy badge in the UI — that's intentional, not a gap.

**Follow-up bug found immediately after, from user spot-checking the dashboards live: Grid Stacker's stream-level test used the wrong capital basis.** Blended mode's `lot_size_usd` IS the total capital pool (`slot_capital_weight` splits it into per-slot amounts) — `model_engine.py` already special-cased this correctly, pulling the real $100 from `backtest.model_streams`. But the cleanup script's stream-level rerun used the generic $20-per-lot convention (correct for every other, single-slot stream) as if Grid Stacker were one too — so it ran as $20 total → $4/slot instead of $100 total → $20/slot. Annualized-return/win-rate/drawdown percentages were coincidentally unaffected (fees and price gains scale proportionally with capital), but every **dollar figure** — ending balance, per-blend capital in/out, buy/sell fee amounts — was off by 5x. Fixed: `cleanup_and_rerun_fees.py` now derives blended-mode capital from `backtest.model_streams` instead of assuming the generic per-lot default. Re-ran and replaced (not duplicated) Grid Stacker's 5 `stream_tests` rows and Model 3's 5 `model_tests` rows on the corrected $100 basis — same `model_test_id`s (137–141), just refreshed. Commit `4b01f6a`.

**Second follow-up, same live spot-check: Model Tester was missing the blend/compound detail Stream Tester had.** Model Tester's Combined Trade Log rendered blended-stream trades in a flat generic table — misleading, since a blended trade's `capital`/`entry_price` are pooled/averaged across multiple fills, not a single fill like every other stream. Stream Tester already had `_render_blended_trade_log` (per-fill breakdown, buy/sell fee split, compounding note) for this. Fixed: `model_dashboard.py` now detects any stream with `slot_mode=='blended'` and renders it with the same reused function, excluded from the flat combined log to avoid double-counting. Also fixed a hardcoded `"Model 1"` label in the page header (was wrong for every other model, including Model 3, which this bug was found while viewing). Verified by directly invoking `render_model_dashboard()` against Model 3's real saved payload outside Streamlit — no exceptions, blended stream correctly detected. Commit `b99e814`.

#### 5. ✅ The deeper live fee-accounting gap — DONE (2026-08-03, latest session)
Live code never captured Kraken's real per-trade fee/fill-price, only estimated via constants. Fixed — see the "Real per-trade fee capture" entry at the top of "Done This Session" above for the full story, including the mixed-legacy-fee bug found during re-verification and the fee-drift-healthcheck wiring gap found on `live-model-3` along the way.

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
| `order_manager.py` / `blended_order_manager.py` | Live P&L only ever used `MAKER_FEE`/`TAKER_FEE` constants, never Kraken's real per-trade fee; exits used the theoretical stop-trigger price, never a confirmed real fill | Poll `get_order_status()` once, synchronously, right after every exit's market sell; use real price/fee, falling back to the estimate (flagged `fee_is_estimated`) only if that poll doesn't confirm yet |
| `blended_order_manager.py` | No entry-side fee was tracked anywhere (slot 1 or cascade adds) — only the exit-side `TAKER_FEE` was ever subtracted | Every fill's real fee now recorded in `live.blended_fills.fee_usd`, summed at exit |
| `blended_order_manager.py` (`place_exit`) | `SELECT SUM(fee_usd)` silently skips `NULL` fills (legacy, pre-migration) — would undercount a mixed legacy/real position's true fee total and leave `fee_is_estimated` incorrectly `False` | Walk fills individually; estimate only the specific fill missing real data, flag estimated if any fill needed the fallback |
| `blended_healthcheck.py` (all branches) | Fee-drift check only ever ran from Model 1's healthcheck, never Model 3's own | Wired `check_fee_drift()` into `blended_healthcheck.py` too |
| `fee_check.py` / `healthcheck.py` | Assumed one shared Kraken account meant checking fee drift from one healthcheck covered both models — false, since each live branch keeps its own independent constants copy | `check_fee_drift()` now runs from both `healthcheck.py` and `blended_healthcheck.py`, on every branch |
