# ADR 009 — Unify Testing With Live Execution (No Separate Backtester)

**Date:** 2026-08-09 (mandate) / 2026-08-10 (plan, then built same session)
**Status:** Steps 1-4 built and validated on branch `unify-testing-live-replay`
(not merged). `engine.py` still can't be deleted — see "What's still on
`engine.py`" near the bottom before assuming otherwise.

## The problem, stated plainly

This project has two independent, hand-written implementations of "what a
position should do": `src/backtester/engine.py` (fast, vectorized,
approximate) and the live execution modules (`order_manager.py`,
`position_monitor.py`, and their blended-mode equivalents — slower, real,
authoritative). Nothing enforces that they agree. History:

- **The severe incident**: a backtested candidate showed ~95% annualized
  return; brought toward live, actual behavior was negative. Root cause
  (2026-08-05 session, see HANDOFF.md): the backtest was crediting phantom
  fills — sales at prices the market never actually touched — inflating
  blended-mode results 10-20x. Live-replay against the real order-management
  code showed the true picture: a 49% loss rate and capital frozen below
  the minimum tradeable lot.
- **This week's incidents** (2026-08-07, see docs/decisions/008 and
  HANDOFF.md): silent, unconditional compounding in backtest that live never
  did; `max_hold_candles`/`min_hold_candles`/`stop_loss_pct` fully
  implemented in backtest and completely absent from live position
  monitoring, live for real money on Model 1's Dip Hunter stream the entire
  time undetected.

**Pattern**: every one of these was a parameter or mechanic that existed on
one side and not the other, discovered only by manually running a
side-by-side comparison — which nothing forced to happen every time, so it
didn't. A memory note telling a future session "remember to check" is not a
fix for this. It already wasn't enough once.

## The decision (agreed with the user, 2026-08-09)

**There should not be a separate "backtester" that approximates live
behavior for speed.** One test path. It has to work the same as live —
not "close enough," not "differs by a little" — the same. If a fast,
approximate check is ever useful during stream design, it must be clearly
and unmissably labeled as a rough sanity check, never something a real
deployment number is quoted from. The bar for any number this project
trusts is: **it came from the same code that would actually place the
order.**

This is not a request to patch `engine.py` again. It's a request to
reconsider whether `engine.py`'s entire approach — a second, independently
maintained simulation of the rules — should keep existing at all, versus
every "backtest" being a live-replay run (like `tools/live_replay/
replay_model.py`, `replay_gauntlet.py`, already built this week) that drives
the REAL `order_manager`/`position_monitor` code against historical data,
full stop.

## What a plan for this needs to work out (not decided yet — for next session)

1. **What replaces `engine.py`.** The live-replay tools built this week
   already prove the concept: drive real live code against historical
   `market_data`, get real trade-by-trade behavior. The open question is
   whether `engine.py` is deleted entirely, or kept only as an
   explicitly-labeled fast/approximate pre-filter for parameter sweeps
   during stream design — never a source of a trusted number, and if kept,
   probably not even called "backtest" anymore given how loaded that word
   now is here.
2. **Speed.** Live-replay is slow — a single 4-stream, ~4.5-year run took
   several minutes today. Stream design involves trying many parameter
   variations quickly. A plan needs a real answer for this that doesn't
   quietly reintroduce a second, faster-but-different code path (the exact
   thing being eliminated) — e.g. an in-memory mode of the same
   order_manager/position_monitor functions that skips real DB writes but
   calls the identical rule logic, or accepting slower iteration as the
   real cost of trustworthy numbers.
3. **What's shared vs. what's inherently different.** Live polls wall-clock
   time and reacts to real Kraken order-book fills; a historical replay
   steps through discrete candles. Even calling identical rule code, exact
   fill price/timing will not be bit-for-bit identical between a live run
   and a replay of history — that gap is small, bounded, and already
   measured (16-38% relative cumulative $ drift, always live slightly
   ahead) as of 2026-08-07's live-replay work, categorically different from
   a rule silently not existing on one side. The plan should state this
   distinction clearly so "the numbers will never be dollar-perfect" isn't
   confused with "the rules might not even match."
4. **Multi-slot modes.** Only single-slot streams (Model 1/2's real shape)
   have real live-replay tooling and live execution at all today.
   Staggered/cascade/blended have varying degrees of live-side
   implementation (blended is furthest along; staggered/cascade have
   backtest logic with, per an earlier finding this session, `executor.py`
   still hardcoding `slot_number=1` for entries). The plan needs to cover
   whether these get built out to the same standard or are deliberately
   descoped.
5. **Migration of everything already "validated."** Model 1 and Model 2's
   current numbers (this session) came from `engine.py` plus a separate
   live-replay confirmation pass — not from one unified path. Under the new
   architecture, they'd need to be re-validated the new way before being
   trusted again, not grandfathered in.
6. **What happens to `docs/decisions/008`'s BTC bucket work.** Built this
   week with real live order-placement code (`bucket_manager.py`) but only
   unit-tested plus one smoke test, never run through a live-replay-style
   historical confirmation. Whatever the new unified architecture looks
   like, the bucket needs to go through it before anything built on it is
   trusted, same as everything else.

## The plan (decided with the user, 2026-08-10)

Answers to the six open questions above, in order:

1. **`engine.py` is deleted entirely.** No fast/approximate pre-filter kept
   around in any form. Every backtest becomes a live-replay run driving the
   real `order_manager`/`position_monitor` code. One code path, no carve-out.
2. **No in-memory mode. No fast path at all.** Profiled `replay_model.py`
   for real (Model 1, 3 streams, 1 year, 8,781 ticks) before deciding this:
   103s wall time, ~54% of it (~56s) inside Postgres `cursor.execute`/
   `commit` — one real transaction per tick per stream — versus 6.9s for
   `engine.py`'s equivalent vectorized run. So the speed gap is I/O, not
   rule-logic cost, and an in-memory backend would have been cheap to build.
   The user chose to accept the slower iteration anyway rather than
   maintain a second storage backend for `order_manager`/`position_monitor`
   — even an in-memory one is a second code path that could silently drift
   from the real one. Stream design sweeps are just slow now; that's the
   accepted cost of trustworthy numbers.
3. **Resolved by #1.** With no second rule implementation, "shared vs.
   different" collapses to the fill-timing/price gap alone (candle-step
   replay vs. real wall-clock Kraken fills) — already measured at 16-38%
   relative cumulative $ drift, always live slightly ahead, and accepted as
   a bounded, expected property of replay, categorically different from a
   rule not existing on one side.
4. **Staggered and cascade slot modes get built out to full live parity**
   (real `order_manager`/`position_monitor` support, plus replay tooling),
   same standard as single-slot and blended, before any stream using those
   modes can be trusted again. Not descoped. `executor.py`'s hardcoded
   `slot_number=1` for entries is in scope for this work.
5. **Nothing is grandfathered in — re-validate everything, including
   Model 1 (live, real money).** Model 1's current numbers, Model 2's Run 3
   composition, and Model 3's BTC bucket (docs/decisions/008) all get
   re-run through the unified live-replay-only path once it covers their
   slot modes, before being trusted again for any further decision. This
   explicitly includes the model already running live — being live already
   doesn't exempt it.
6. **BTC bucket is covered by #5** — same re-validation requirement as
   Model 1/2, no separate carve-out.

7. **Stream-level live-replay results get persisted at trade level, not just
   aggregate metrics, and model assembly reuses them instead of re-simulating.**
   Found this session: `backtest.stream_tests` today only stores summary
   numbers (`win_rate`, `total_pnl`, etc. — `schema.sql:129-175`), never raw
   trades, so `run_model_backtest`/`run_pooled_model_backtest`
   (`model_engine.py:13,116`) have no choice but to call `run_backtest()`
   fresh per stream every time a model is assembled or re-viewed, even for
   an already-locked, already-tested `stream_config`. That's real wasted
   work for the common case. Fix: persist trade-level rows for a
   stream-level live-replay run too, keyed the same way `stream_tests`
   already dedups — `(stream_config_id, preset_id)`. Then:
   - **Non-pooled models** (fixed `lot_size_usd × slot_count` per stream, no
     shared reserve — Model 1 and Model 2's actual shape): streams don't
     interact, so model assembly pulls each locked stream's already-computed
     trades and recombines them into portfolio metrics. Zero re-simulation,
     and the model-level number is *guaranteed* to be built from the exact
     trades already validated at lock time, not a fresh run that could
     theoretically diverge from what was locked.
   - **Pooled/shared-capital models** (`live.capital_reserve`, the BTC
     bucket from docs/decisions/008): still require one real joint
     live-replay run per composition. Streams compete for shared capital
     there, so a stream's isolated locked-test trades assumed capital that
     may not actually have been available at that moment — that interaction
     can't be decomposed from independent per-stream results, only from a
     real joint run.

### Status: step 1 done, step 2 in progress — cascade needs more validation before it's trustworthy

Step 1 (staggered/cascade live parity) is built and unit-tested (62 tests,
`tests/live/test_staggered_cascade.py` new). While extending replay tooling
(step 2) to smoke-test it against a real locked composition (staggered
config `stream_config_id=9`, cascade config `stream_config_id=18`, neither
used by any live or about-to-deploy model — see item #6), found and fixed a
real, general gap this exercise was meant to catch: `trailing_stop_steps`
and `trail_arm_gain_pct` were read by `engine.py` for every slot mode but
had **no live implementation at all** — `position_monitor.py` only ever
applied the flat `trailing_stop_pct`. Confirmed via direct query that no
currently-locked Model 1 or Model 2 stream config sets either param, so this
was not an active live-money bug, but it would have silently bitten the next
config that did. Fixed and covered by two new tests. Also fixed a real sign
error in the ladder-stop port: it was armed at `deeper_slot.entry_price *
(1 + buffer)` (wrong direction and non-ratcheting) instead of `engine.py`'s
actual mechanic — armed at the deeper slot's entry price and then ratcheting
up with that slot's own high-water mark, `(1 - buffer)`.

After both fixes, staggered matched `engine.py`'s trade count exactly (20
vs 20, `Momentum Rider v3`, 2021-2024). **Cascade did not** (`Cascade DCA
v2`: 19 backtest vs 26 live-replay, same window) — investigated the obvious
candidates (instant same-tick fill semantics, trigger re-arm logic) and they
check out as equivalent to `engine.py`'s design. Working theory, not yet
confirmed: multi-slot cascade is a new category of divergence beyond the
already-accepted small $ drift (item #3) — a slot's exit-timing landing on a
different candle by even one tick can flip whether a *later* signal finds
that slot free or occupied, and unlike single-slot mode (where timing noise
doesn't gate anything downstream), a cascade/staggered stream's future
entries structurally depend on current slot occupancy, so small timing noise
can compound into real trade-count divergence over a long window without any
rule actually being wrong. This needs to be run down with more replay
windows (and against `Cascade DCA v1`, `Grid Stacker`'s cascade-shaped
configs) before cascade specifically is trusted — **not currently blocking**
since nothing live or locked depends on it, but do not lock or deploy a real
cascade stream config until this is resolved. Staggered's exact match is a
good sign it's the cascade-specific mechanics (trigger-arm interaction with
exit timing) at fault, not something wrong with the shared slot-dispatch
plumbing.

**Cascade live parity removed, 2026-08-10.** Per the user: don't carry an
unvalidated code path forward just because it was already written — it was
the one thing blocking `engine.py`'s eventual deletion, and no model needs
it today. Removed `order_manager.check_cascade_triggers`, `position_monitor`'s
ladder-stop mechanic, `executor.py`'s call into it, and `replay_model.py`'s
cascade support (back to raising on `slot_mode='cascade'`, same as before
this session). Staggered live parity is kept — it validated cleanly (exact
trade-count match against `engine.py` for both the synthetic `Momentum
Rider v3` case and, more importantly, Model 1/2's real streams below).
`engine.py`'s own `_run_cascade_slots` backtest logic is untouched (it
predates this session and `engine.py` isn't deleted yet) — but per the
mandate, a `stream_config` with `slot_mode='cascade'` has no live execution
path and must not be locked or deployed until this is rebuilt properly, with
more time than a same-session validation pass affords.

### Step 3 done (differently than planned) — trade-level cache built, opt-in only after a real staleness finding

Item #7 turned out simpler to build than planned: `stream_tester.py`'s
`_run_and_save` already pickles the full trades DataFrame into
`src/app/runs/{test_id}.pkl` for every stream test ever saved (`payload
["trades"]`) — no new schema/table needed. Added `model_engine.
_cached_stream_trades(stream_config_id, start, end)`, looked up by matching
either a custom-range `stream_tests` row directly or a preset row via
`timeframe_presets`, and wired it into `run_model_backtest` (the non-pooled
combiner only — `run_pooled_model_backtest` always stays fresh, per item #7).

Found two real bugs verifying this against Model 1's actual locked streams
before trusting it:
1. **Capital mismatch**: a stream-locking test's capital is a placeholder
   (CLAUDE.md), routinely different from the model's real allocation ($20
   test vs. Model 1's real $33.33). Fixed: `_cached_stream_trades` returns
   the capital the cached run used alongside the trades, and the caller
   rescales `capital`/`pnl` to the model's real `lot_size_usd` (valid since
   % return per trade is capital-independent — only entry/exit price/timing
   columns are left untouched).
2. **Staleness, no invalidation**: after fixing #1, Model 1's Breakout Scout
   v2 still mismatched real trade-for-trade — its cached test (`test_id=172`)
   turned out to predate the 2026-08-07 unconditional-compounding fix (see
   the historical section above) and was still showing compounding capital
   under params that no longer set `compound=True`. The cache key is only
   `(stream_config_id, start, end)`, with **no tie to what version of
   `engine.py` (or which fee constants) computed it** — a `stream_tests` row
   saved before any future engine fix will be served forever as if still
   valid, since nothing currently re-runs or flags it. No general fix for
   this exists yet (would need real versioning/invalidation, out of scope
   this session). Mitigation: `run_model_backtest`'s `use_cache` defaults to
   **False** — reuse is opt-in, safe for fast exploratory iteration during
   design (the actual "why are we re-running this" case this was built to
   fix), but a number that gates a real deployment decision must always come
   from a fresh run, same as before this existed. Covered by
   `tests/backtester/test_model_backtest_cache.py` (rescale math + the
   safe-by-default flag).

### Step 4 done, scoped down — Model 1 and Model 2 both pass; everything else in `stream_tests`/`model_tests` is disowned, not re-validated

Per the user (2026-08-10): all existing backtest data is considered
untrusted until it's been run through this unified path — not worth
individually auditing or preserving. Rather than re-validate every historical
`stream_tests`/`model_tests` row, the only two things that actually matter
are the streams that made it into a real model: **Model 1 (live, real money)
and Model 2 (Run 3, assembled not deployed)**. Everything else on record is
old data that gets regenerated fresh whenever that stream/model is worked on
again, not retroactively fixed. The BTC bucket (docs/decisions/008) is
deliberately NOT included in this pass — it's Model 3's build, out of scope
for this Model-1/2-focused check.

Ran `tools/live_replay/replay_model.py` for both, full Primary v2 window
(2022-01-01 → 2026-08-09, ~4.5 years):

| Model | Stream | Backtest ref. trades | Live-replay trades | Match |
|---|---|---|---|---|
| 1 | Breakout Scout v2 | 16 | 16 | exact |
| 1 | Dip Hunter v2 | 20 | 20 | exact |
| 1 | Momentum Rider v2 | 31 | 31 | exact |
| 2 | Breakout Scout v3 | 20 | 20 | exact |
| 2 | Dip Hunter v3 | 20 | 20 | exact |
| 2 | Momentum Rider v4 | 29 | 29 | exact |
| 2 | Volume Raider v1 | 39 | 38 closed + 1 still-open = 39 | exact |

Model 1 totaled 67 trades / $72.98 realized on $100 baseline — matches the
recorded `model_tests` row for this exact preset (67 trades) exactly; $
annualized landed a bit above the recorded 10.59% (~12.7%), consistent with
the already-accepted small mechanical drift (item #3), not a rules gap.
Model 2 totaled 108 trades (matching its recorded row exactly) / $91.98
realized. **Both pass at the bar that actually matters — trade count, not
dollar-perfect match — with zero divergence.** This is exactly the
single-slot-only case that was already expected to be clean; it does not
touch the open cascade question above.

One operational note, not a code bug: running two `replay_model.py`
invocations in parallel crashed one of them — both use the same hardcoded
sandbox `model_version=993`, so a concurrent run's cleanup step deletes rows
the other run is still using. `replay_model1.py`/`replay_gauntlet.py`
already document this same "don't run both at once" constraint for their
own sentinels (991/992); `replay_model.py` needs the same warning added to
its docstring (not yet done).

### Execution order

`engine.py` can't be deleted on day one — staggered/cascade have no other
spec to build live parity from yet, so it's the reference during that
build-out, explicitly demoted to "spec to port from," never "source of a
trusted number" per the mandate. Order:

1. Build staggered/cascade live parity (`order_manager.py`/
   `position_monitor.py`, fix `executor.py`'s hardcoded `slot_number=1`)
   using `engine.py`'s existing logic as the spec.
2. Extend `tools/live_replay/` to cover staggered/cascade the same way
   `replay_model.py` already covers single-slot and `replay_gauntlet.py`
   covers blended.
3. Persist trade-level rows for stream-level live-replay runs (item #7);
   rewire model assembly to reuse them for non-pooled models instead of
   re-simulating.
4. Re-validate Model 1, Model 2, and the BTC bucket through the now-complete
   live-replay path (item #5). Model 1 is live real money — this is a
   confirmation pass on the running model, not a redeploy.
5. Only once nothing depends on `engine.py` as a spec or a comparison
   reference anymore: delete it.

Also fix in passing (found this session, unrelated to the ADR but blocking
replay runs): `replay_model.py`'s summary print crashes with
`TypeError: unsupported format string passed to NoneType.__format__` when a
stream backtests zero trades in the window — `compute_metrics` correctly
returns `annualized_return_pct: None` for an empty trade set, the print
just doesn't guard for it.

## Stream Tester converted to live-replay, 2026-08-10

Found via the user directly asking "does it work like production, is
Streamlit cleaned up" — it wasn't. `src/app/pages/1_model_tester.py` was
already fine (no interactive Run button; it only displays saved results
from scripts run externally, matching the intended workflow). But
`src/app/stream_tester.py`'s "Run All Presets"/"Re-run" buttons still
called `engine.py`'s `run_backtest()` directly and instantly — the literal
"separate backtester" this whole ADR is about eliminating, still live and
interactive.

Built `src/backtester/live_replay_stream.py` — generalizes
`tools/live_replay/replay_model1.py`'s proven single-stream pattern (which
only ever printed to stdout) into a reusable `run_live_replay_stream()`
function returning the same shape `run_backtest()` does (`trades`, `df`,
`start`, `end`, `signals`, fees), so it's close to a drop-in replacement.
Drives the real `order_manager`/`position_monitor` code, same as
`replay_model.py`, for ONE unlocked stream in isolation (no model
composition needed) — supports `single`/`staggered` (the only two live has
real parity for). Wired into `stream_tester.py`'s `_run_and_save`: single/
staggered configs now run through this; `blended`/`cascade`/`scale_down`/
`scale_up` (no live-replay path) fall back to `engine.py`'s `run_backtest()`,
explicitly flagged in the saved test's `notes` field as "NOT live-validated"
so that's visible later, not silently indistinguishable from a real number.
Verified against Model 1's actual Dip Hunter v2 and the staggered Momentum
Rider v3 config: trade counts matched `engine.py`'s reference exactly in
both cases (15/15 for the staggered case). Covered by
`tests/backtester/test_live_replay_stream.py`. Slower than the old instant
button (tens of seconds to minutes depending on window) — the outer
"Run All Presets" progress bar now shows per-candle progress; the three
single-preset run/re-run buttons use a plain spinner, not upgraded to a
progress bar this pass.

### What's still on `engine.py` — it is NOT deletable yet

Despite the above, `engine.py` remains a real, load-bearing dependency for
several things, not just a comparison reference:
- `load_market_data`/`_warmup_days` are utility functions that live INSIDE
  `engine.py` but have nothing to do with "the backtester" — `live_replay_
  stream.py`, every `tools/live_replay/*.py` script, and even
  `2_live_monitor.py` all import them from there. These need to move to
  `indicators.py` (or a new module) before `engine.py` can go, regardless
  of anything else.
- `model_engine.run_model_backtest`'s "fresh run" fallback (when the
  stream-trade cache misses, or `use_cache=False`, the default) still calls
  `engine.py`'s `run_backtest()`, not `live_replay_stream.py` — not yet
  converted. Model 1/2's real numbers are still trustworthy because they
  were confirmed through a SEPARATE, real `replay_model.py` run (step 4
  above), not through this fallback path — but the fallback path itself is
  still the old "second implementation," not yet unified.
- `stream_tester.py`'s fallback for `blended`/`cascade`/`scale_down`/
  `scale_up` slot modes (above).

None of this blocks Model 1/2 (both validated for real, separately) or the
Stream Tester's common case (single/staggered now live-replay-backed). But
"delete `engine.py`" is further off than "the mandate's core idea is
implemented" might suggest — treat these as the next concrete steps if that
deletion is still the goal.

## What NOT to do until execution order steps 1-3 are complete

- Don't keep patching `engine.py` for new rules — it's now a spec-only
  reference for staggered/cascade parity (step 1), never a source of a
  trusted number.
- Don't deploy Model 2, or provision `live.capital_reserve`/`live.btc_bucket`
  for any model, until re-validation (step 3) is complete under the new path.
- Don't build new streams or models under the old dual-path testing process.
  Per the user, directly: no point building anything if the test can't be
  trusted.
