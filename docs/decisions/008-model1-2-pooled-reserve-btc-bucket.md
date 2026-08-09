# ADR 008 — Model 1/2 Pooled Capital Reserve + Profit-Skim BTC Bucket

**Date:** 2026-08-07
**Status:** Implemented as a backtest (`run_pooled_model_backtest()` in
`src/backtester/model_engine.py`) — not yet Gauntlet-tested, not wired for
live. First run against Model 2's real composition (2022-01-01→2026-08-05):
plain `compound=False` ends $173.17; pooled reserve + bucket ends **$175.81**
(final pool $143.65 cash + bucket $32.16 — 3 dip-buys, 2 principal
recoveries, still holding real BTC). `skipped_entries: 0` — the floor/shrink
logic never actually bound on this real history, so this first result says
nothing about how it behaves under real stress; that's what the Gauntlet's
bear-market pass is for, still queued (see Next steps).

## Context

2026-08-07's session added a real `compound` flag to the backtester's
single/staggered/cascade slot modes (previously, all three silently
compounded per-slot P&L unconditionally, with no flag — a real bug, see
HANDOFF.md). With the bug fixed and a true `compound=True` path built,
Model 1/2 were re-tested honestly for the first time: **`compound=False`
(fixed lot size, matches live) beats `compound=True` on every stream and at
the model level.** This confirms the 2026-08-06 post-hoc estimate that
compounding hurts Model 2 was correct — now backed by a real implementation,
not a reconstruction.

**Concrete example (Volume Raider v1, real backtested trades, Primary v2):**
a 4-trade losing streak (trades 10–13) shrinks the compounding account from
$30.16 to $21.86 in trading capital. Trade 14 — the single best trade in the
stream's whole history, +57.6% — then fires. Fixed sizing captures the full
$14.40 gain on its untouched $25. Compounding only had $21.86 behind that
same 57.6%, capturing $1.81 less on the one trade that mattered most. Over
all 39 real trades: fixed ends at $44.99, compounding ends at $40.46 — worse
by capturing sequence-of-returns/volatility drag, not by any difference in
average trade quality.

**Consequence: with `compound=False` confirmed correct, every dollar of
realized gain beyond a stream's fixed lot size is currently just idle cash**
— it isn't reinvested into anything, isn't compounding, isn't doing
anything at all for the rest of the model's life. This is a materially
different situation from Model 4's rejected profit-skim bucket (doc 007
section 4), which skimmed from a strategy that *was* compounding, so every
skimmed dollar was pulled out of a pool already beating buy-and-hold —
a real opportunity cost. **Model 1/2 have no such pool to cannibalize.**
Skimming idle cash into BTC has no downside the compounding case had.

## Decision

Reuse doc 007 section 4's tuned bucket mechanics (`simulate_skim_bucket()` /
`dynamic_skim`, recoverable via `git show bb18d45:src/backtester/engine.py`)
for Model 1/2, but fund it from a **pooled model-level cash reserve**, not a
naive per-trade skim straight off each stream's own gains. The naive version
was missing a real gap: nothing today verifies a stream's real cash balance
before placing an order at its fixed lot size — a losing streak can leave
less real cash behind a stream than its configured lot size, even though
trade *size* itself never shrinks under `compound=False`.

### Mechanics

- **Baseline** = sum of the model's configured `lot_size_usd` across all
  streams (e.g. $100 for a 4-stream-line model like Model 2, using $25/each
  as the illustrative round number — actual Model 2 allocation is uneven,
  see [[project_capital_allocation]]).
- **Pool at or above baseline**: every stream trades at its own configured
  fixed lot size, unchanged — this is exactly the validated `compound=False`
  behavior, preserved on purpose. No stream's trade size grows just because
  the pool grew above baseline.
- **Pool below baseline**: each stream's effective trade size shrinks
  proportionally to its own configured *share* of the pool (not an equal
  split across streams) — e.g. a $25-lot stream shrinks by more real dollars
  than a $12.50-lot stream at the same pool depletion %, preserving the
  model's designed relative weighting.
- **Hard floor** = sum of each stream's own $10 minimum (CLAUDE.md's stated
  floor) — model-specific: $40 for a 4-stream-line model, $30 for Model 1's
  3 streams. Below the floor for the *pool as a whole*, that's a hard stop,
  not further shrinking — no trade should ever be sized under $10.
- **Per-stream reactivation, kept simple**: if a single stream's own
  allocated share dips below its own $10 floor (while the rest of the pool
  is still healthy), that stream just pauses. The moment pooled winnings
  bring its share back above $10, it resumes trading immediately on the next
  signal — no extra gating, no cooldown.
- **The BTC bucket only ever skims the surplus once the pool is back at or
  above baseline** — realized gains rebuild the pool to baseline first;
  only gains beyond that are eligible for `dynamic_skim`'s cut.

### Why this is not the same mistake as Model 4's rejected version

Model 4's bucket skimmed from capital that was actively compounding inside a
strategy already beating buy-and-hold — a real, measured opportunity cost
(doc 007: Full History -30.4%, Primary v2 -7.7% vs. main-stream-only).
Model 1/2 don't compound (validated 2026-08-07, both by the flag itself and
by the Volume Raider example above) — realized gains beyond the fixed lot
are provably idle otherwise. The reserve/floor mechanics above exist to
guard real account solvency (a genuine, independent gap — nothing today
checks real cash before placing an order at all), not to protect a
compounding pool that doesn't exist in this design.

## Implementation notes (`run_pooled_model_backtest()`, `src/backtester/model_engine.py`)

Single-slot streams only (raises if given a multi-slot/blended config) —
matches Model 1/2's real composition. Two-pass design: each stream is first
backtested normally at its full `lot_size_usd` via the existing
`run_backtest()` to get real `(entry_ts, exit_ts, roi)` triples (valid
because entry/exit *timing* in this codebase is always %-based, never
$-based — capital size never changes when or whether a trade fires, only how
much money is behind it). Pass two walks every stream's entries/exits merged
into one true chronological timeline against a single shared `pool_balance`,
applying the shrink/floor/skim rules above, then feeds the resulting skim
timeline into the unmodified `simulate_skim_bucket()` dip-buy/recover-
principal mechanics from `bb18d45`.

First run, Model 2's real composition, 2022-01-01→2026-08-05: plain
`compound=False` (no bucket) ends $173.17; pooled reserve + bucket ends
**$175.81**. `skipped_entries: 0` — the floor/shrink logic never bound on
this real history, so this result doesn't yet validate that mechanic under
stress.

## Gauntlet results (2026-08-07, `tools/gauntlet_model2_pooled_bucket.py`)

**Broad robustness checks pass.** Part 1 (single-year walk-forward,
2018-2026): 6/9 years positive (2018, 2022, 2025 negative — reasonable
variance, not a red flag on its own). Part 2 (10,000-resample bootstrap on
the pool-only sequence, bucket excluded — see script docstring for why the
bucket can't be honestly bootstrapped): real historical result lands at the
**50th percentile** of the distribution (i.e. the actual history was
neither lucky nor unlucky relative to the same trades in random order),
only 1.45% of resamples ended below the $100 baseline. Part 4 (code review):
no blockers found; single-slot-only guard, tie-break ordering, and the
surplus-only skim math all check out against the design — full detail in
the script's own PART 4 output.

**The one finding that actually matters: `skipped_entries: 0` in every
single test run** — the full 8.6-year history, all 9 individual years, and
all 4 dedicated real bear-market windows (2018 crypto winter, full-year
2022, Terra/FTX peak-to-trough, COVID crash). **The floor/shrink mechanic —
the entire point of this design, the actual safety feature — has never
once been exercised by any real historical data tested so far.** This is
not necessarily bad news (it may mean the floor is a legitimately rare-case
backstop that this model's real drawdowns don't come close to needing), but
it means **zero evidence exists yet for how the mechanism behaves when it
actually engages.** All bear-market windows here start fresh at the $100
baseline — none of them test a stream already-depleted from a prior bad
stretch compounding into a second one, which is the actual scenario the
floor exists to protect against.

Bucket's real contribution in bad years was small and appropriately
conservative ($0.67–$2.45 vs. $126.06 over the full favorable-period
history) — it isn't skimming much when there's little surplus to skim,
which is correct, expected behavior, not a flaw.

## Deadlock found and fixed (2026-08-07, Gauntlet Part 5)

The stress test (`starting_pool` below baseline against real 2022 signals)
surfaced a real design flaw, not just an untested code path: below the
aggregate point where **every** stream's proportional share drops under
$10 simultaneously (all four of Model 2's streams are equally weighted, so
this happens for all of them at once), the pool doesn't just slow down —
**it freezes permanently.** No stream can trade, so no stream can produce
the winning trade needed to lift the pool back above $10 on its own. The
original design's assumption ("winnings bring it back above the floor,
resume trading") silently assumed something would still be trading to
produce those winnings — false once every stream pauses at the same time.

**Decision: treat this as a hard stop requiring a real alert, not an
auto-recovery mechanic** — manual capital top-up, or a decision to pull the
underperforming stream/model entirely, not something the algorithm should
try to solve itself. `run_pooled_model_backtest()` now computes
`hard_floor = 10.0 / max(stream weights)` and returns `halted_at` (`None`,
`"start"`, or the real timestamp trading first became fully impossible) so
this state is a first-class, alertable signal rather than something that
silently shows up as a flat pool balance. Verified: stays `None` while the
pool degrades gracefully (`$45` starting pool, 2022 bear year), fires
correctly the instant the true floor is crossed (`$41` → halts
2022-06-13; `$38` → halts immediately, `"start"`).

## Next steps (not started)

1. ~~Construct a deliberate synthetic stress test~~ — **done, see "Deadlock
   found and fixed" above.**
2. Live currently has **no capital-availability check at all** for
   single/staggered/cascade streams — `order_manager.place_entry` always
   sizes off the configured `lot_size_usd` regardless of real account
   balance. Building a real pooled reserve ledger (`live.*` table, mirroring
   `live.blended_capital`'s existing pattern) fixes this independently of
   whether the BTC bucket ships at all — worth treating as its own
   deliverable, and arguably higher priority than the bucket itself.
3. **Wire `halted_at` to a real alert once live** — this needs to page a
   human (same notifier pattern as `alert_system_down`/`alert_market_data_stale`
   in `src/live/notifier.py`), not silently log. The alert should prompt a
   real decision: inject capital, or pull the underperforming stream/model —
   hitting this floor at all is itself a signal something in the model's
   real performance has gone seriously wrong, not just a capital mechanics
   footnote.
4. Add at least one deterministic unit test for `run_pooled_model_backtest()`
   itself — flagged in the Gauntlet's Part 4, no backtester-level test suite
   exists in this project at all currently (pre-existing gap, not introduced
   here). Should include a fixture covering the `halted_at` deadlock case
   directly, not just via a manual stress-test script.
5. If the above holds up: build live wiring (skim execution, real BTC
   buy/sell orders for the bucket, the reserve ledger from step 2) — a
   materially larger scope than anything else in this doc, not started.
