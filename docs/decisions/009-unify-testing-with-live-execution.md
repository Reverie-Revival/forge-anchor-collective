# ADR 009 — Unify Testing With Live Execution (No Separate Backtester)

**Date:** 2026-08-09
**Status:** Mandate accepted, plan not yet written — deliberately deferred to
a dedicated future session. Nothing in this document should be implemented
without a full planning pass first.

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

## What NOT to do before the planning session

- Don't keep patching `engine.py` piecemeal (compounding flag, exit rules,
  etc.) as if the two-implementation architecture is staying — every one of
  those patches is now provisional pending this decision.
- Don't deploy Model 2, or provision `live.capital_reserve`/`live.btc_bucket`
  for any model, until this is resolved — today's validated numbers are
  legitimate under the current process, but the process itself is what's
  being reconsidered.
- Don't build new streams or models under the current dual-path testing
  process. Per the user, directly: no point building anything if the test
  can't be trusted.
