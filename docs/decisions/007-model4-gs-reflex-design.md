# ADR 007 — Model 4 (GS: Reflex) Design

**Date:** 2026-08-04
**Status:** Accepted — v2 shipped to production (2026-08-05), replacing Model 1

## Decision
Ship **GS: Reflex v2** as Model 4: Grid Stacker Blended's mechanics (Model 3,
unchanged) plus four adopted reactive layers — capitulation ladder,
sentiment-tilted slot weighting, slot promotion, and a shallow breakeven
margin. Single stream, mirroring Model 3's design (a second stream was
explored, see section 9, and parked).

**What shipped vs. what stayed archived**: the production `_run_blended_slots`
(`src/backtester/engine.py`) carries only what v2's config actually uses —
compound, trailing stop + arm, cumulative cascade drops, the capitulation
ladder, sentiment tilt, slot promotion, and the shallow breakeven margin —
plus `capitulation_stop_pct`, kept because Model 3 (v8, live) still uses that
legacy single-line form. Every rejected or unexercised idea below (concurrent
stacks, growing/dynamic stacks, the profit-skim bucket, wide-trail,
volatility-adaptive arm/trail, the `prior_slot` promotion anchor, the
ladder's start/step shorthand) was removed from the production code in the
same cleanup pass — this document is the only remaining record of them, kept
for the reasoning, not because the code still supports them.

The rest of this document is the working exploration log below, preserved
as-is for the full derivation of every tested and rejected idea.

---

Stream name: **GS: Reflex** (decided 2026-08-04 — "GS" nods to the Grid
Stacker Blended lineage this is built on; "Reflex" for the reactive layer on
top — reads sentiment, reads stagnation, reads how deep a real crash is,
rather than following one fixed script). **All three adopted mechanisms are
part of the named config**: capitulation ladder + sentiment-tilted slot
weighting + slot promotion, composed together (see the "Full Model 4 combo
vs. v8" comparison in section 5 for the combined backtest numbers).

Working notes for ideas explored toward a Model 4 candidate, built on top of
Grid Stacker Blended's mechanics (blending, slots, compounding — Model 3, staying
unchanged as-is). Nothing here is deployed or wired into the DB; this branch is
pure backtester exploration. Update this file as new ideas get tried so nothing
has to be re-derived in a future session.

## Status summary

| Idea | Status | Verdict |
|---|---|---|
| Capitulation ladder | Implemented, tested | **Leading candidate** — adopt |
| Sentiment-tilted slot weighting | Implemented, tested, tuned | **Adopt, composes with the ladder** |
| Fixed 2 concurrent stacks | Implemented, tested | Tried, not adopted |
| Growing/dynamic stacks | Implemented, tested | Tried, not adopted (clear underperformance) |
| Profit-skim satellite BTC bucket | Implemented, tested | Tried, not adopted (worsens overall return) |
| Slot promotion (impatience trigger) | Implemented, tuned | **Adopt — part of GS: Reflex** |
| Shallow breakeven margin | Implemented, tuned | **Adopt — GS: Reflex v2** |
| Wide trail on deep winners ("let winners run") | Implemented, tested | Tried, not adopted (worse in every preset) |
| Volatility-adaptive arm/trail | Implemented, tested | Tried, not adopted for v2 (real utilization win, but fewer trades — flagged for later) |
| Second stream (range_breakout) | First-pass tested | Parked — v2 alone is the plan; concept validated but not tuned |
| "Always-on" slot 1 (no entry signal) | Tested | Curiosity check — competitive but not better; v2 (fear_dip) stays |
| Stream name | **Decided: GS: Reflex** | Done |

## 1. Capitulation ladder (adopt this)

Problem: the live Model 3 config (`capitulation_stop_pct=15`, single-shot) took
one real -21.18% loss in the full 8-year backtest (Oct/Nov 2025) — a single hard
line, all-or-nothing.

Design: once all 5 slots fill, progressively mark down the *oldest* slot's cost
basis (paper only — no real sale, real shares/cash untouched) at each rung
crossed. This only lowers the *synthetic* average used to gate the
arm/breakeven trailing-stop exit, so a partial bounce is enough to trigger a
real sale at a much smaller loss than waiting for a full recovery. One rung past
the last slot being marked is a real, unconditional final cut — same backstop
role `capitulation_stop_pct` plays today, just reached after 5 chances at a
cheaper bounce-triggered exit instead of one line.

Implemented in `src/backtester/engine.py` `_run_blended_slots()`, opt-in via
new position params (mutually exclusive with `capitulation_stop_pct`, fully
backward compatible — existing configs untouched):
```
"capitulation_ladder_pcts": [20, 22, 24, 26, 28],   # % below slot 1's entry, one per slot
"capitulation_ladder_final_cut_pct": 30,             # % below slot 1 — real, unconditional exit
```

Iterated through several shapes (anchored to slot 5 vs slot 1, uniform 5%
steps, decelerating steps) — settled on the tight version above because
performance was statistically identical across every variant tested (the real
losses in 8 years never got past mark 1-2 either way) while the worst-case
final-cut floor is meaningfully shallower (-27.3% vs -42.9% for a wider
uniform version) at zero measured cost.

Results vs. v8 baseline, real 8-year history:

| Preset | v8 ann% / max DD | Ladder ann% / max DD |
|---|---|---|
| Full History | 59.01% / -21.18% | **62.65% / -2.10%** |
| Primary v2 | 34.62% / -21.18% | **40.50% / -2.10%** |
| Recent | 31.09% / -21.18% | **41.40% / -2.10%** |
| 2026 YTD | 13.65% / -0.00% | 13.65% / -0.00% (loss predates this window) |

The one real Oct/Nov 2025 loss shrinks from -$1,282.64 to -$127.03 (real exit
fires 3 days earlier, off a small bounce, only 1 of 5 marks ever used). The
final-cut backstop has never fired once in 8 years of data — pure insurance.

32/32 `tests/live/` still pass (same pre-existing unrelated
`test_signal_parity.py` collection error, not touched here).

$100 worked example (fills at $100/$99/$98/$95/$90, tight config):

| Step | % below slot 1 | Price | Synthetic avg | Bounce needed to exit | Real $ loss if it fires there |
|---|---|---|---|---|---|
| — (real avg) | — | $90.00 | $96.26 | — | — |
| 1 | -20% | $80.00 | $92.41 | $93.16 | -$3.23 (3.2%) |
| 2 | -22% | $78.00 | $88.33 | $89.04 | -$7.50 (7.5%) |
| 3 | -24% | $76.00 | $84.00 | $84.68 | -$12.03 (12.0%) |
| 4 | -26% | $74.00 | $79.75 | $80.39 | -$16.49 (16.5%) |
| 5 | -28% | $72.00 | $75.90 | $76.51 | -$20.52 (20.5%) |
| Final cut | -30% | $70.00 | — | (unconditional) | -$27.28 (27.3%) |

**Open items**: named — GS: Reflex (see top of file). Not yet assembled as a
locked `backtest.stream_configs` row — still informal on this branch.

## 1b. Sentiment-tilted slot weighting (adopt, composes with the ladder)

Idea: use the daily Fear & Greed reading (`sentiment_data`, unused elsewhere in
this project until now) at the moment a position opens to skew how the $80
across slots 2-5 gets split, instead of always flat $20 each. Slot 1 stays
flat (keeps entry itself simple/unconditional, no gating, no effect on trade
frequency) — only the *distribution* across 2-5 changes.

**The key open question going in**: does more extreme fear mean "front-load,
bet big now" (this dip is the real deal) or "back-load, hold powder" (fear
often precedes more pain, not just marks the bottom)? Tested both directions
against real history rather than reasoning it out — **back-loading wins
clearly and consistently**, front-loading loses in every window tested.

Mechanism, implemented in `_tilted_slot_weights()` in `engine.py`, config via
`position.sentiment_tilt` (also requires `params["sentiment"] = {"fear_greed": {}}`
to load `fng_value` into the dataframe — empty `fear_greed` dict loads the data
without gating any entries):
```
"sentiment_tilt": {
    "direction": -1,      # -1 = back-load on fear (winner). +1 = front-load (loses).
    "strength": 0.4,      # tuned peak -- swept 0.2-0.8, 0.3-0.4 is the real sweet
                           # spot, decays on both sides, not "more is better."
}
```
Locked in once at slot 1's entry candle, not re-evaluated per fill. At
strength=0.4, extreme fear (F&G≈10) roughly splits 2-5 as $10/$16/$23/$31
instead of flat $20 each; extreme greed does the mirror opposite (front-loads).
Neutral (F&G=50) always reduces to the flat baseline regardless of strength.

**$10 minimum lot floor**: `min_factor=0.5` (a 0.5x multiplier on the $20 base
= exactly $10, CLAUDE.md's stated minimum) clamps how far any single slot can
be skewed down. Before this was added, 3 of 1,232 fills in the Full History
backtest went as low as $7.86 — a real violation of the project's stated
constraint, now fixed at zero measurable cost (min_factor was 0.15 before).
Floor near-misses (down to ~$9.79-9.88, a few cents under due to normal
compounding drift, not a bug) only ever occur in the first few trades of a
preset before the pool has built any cushion above its $100 start — confirmed
directly against the real trade sequence, self-heals as soon as a real gain
compounds in.

**Trend context was tried as a second factor and made things WORSE, not
better** — tested multiplying `strength` by a below/above-200-SMA multiplier
(stronger back-load in a downtrend, dampened/off in an uptrend). Underperformed
plain F&G-only in every single variant and preset tested. Likely cause: most of
this 8-year history sits above the 200 SMA (BTC's long-run uptrend bias), so
dampening the tilt there throws away the benefit during the strategy's actual
bread-and-butter trades. Dropped — F&G alone, unfiltered, is the better design.
Other candidates floated but not yet tested: drawdown-from-high, realized
volatility/ATR regime, volume (this last one could plausibly cut the *opposite*
direction from fear — high volume on a drop can mean capitulation exhaustion,
i.e. closer to a bottom, arguing for front-loading rather than back-loading).

Final tuned results (strength=0.4, $10 floor in place), vs. capitulation-ladder
baseline (no tilt):

| Preset | Ladder only | + Sentiment tilt |
|---|---|---|
| Full History | 62.65% / -2.10% | **67.75% / -4.00%** |
| Primary v2 | 40.50% / -2.10% | **46.30% / -2.12%** |
| Recent | 41.40% / -2.10% | **53.95% / -2.12%** |
| 2026 YTD | 13.65% / -0.00% | 13.36% / -0.00% (thin window, 22-23 trades, within noise) |

Consistent, real improvement in 3 of 4 windows; the 4th is a wash on too small
a sample to read either way. Drawdown ticks up a little (still tiny in
absolute terms) — trade-off is real but small next to the return gain.

**Open items**: slot 1 inclusion (`apply_to_slot1`) not tested yet. Other
sentiment-adjacent factors (drawdown-from-high, ATR regime, volume) floated,
not tested.

## 2. Concurrent stacks (tried, not adopted)

User's idea: split the pool into independent parallel blended stacks (Slot 1A,
1B, ... each with its own 5-slot cascade) so idle capital — historically only
~2.6 of 5 slots used per trade, 13.7% of calendar time fully in cash — can catch
a second, differently-timed entry instead of waiting for the current stack to
resolve. Priority order: a fresh signal always tries the lowest-index free
stack first; a higher-index stack only ever opens because a lower one is
already occupied.

Implemented as `_run_concurrent_blended_stacks()` in `engine.py`, wired into
`run_backtest()` via `num_stacks` (fixed split) or `stack_unit_size` (dynamic
growth, see below). Composes with the capitulation ladder (same params).

**Fixed 2-stack result, compared to 1-stack v8 baseline across every preset:**

| Preset | | Trades | Ann% | Max DD | Sharpe |
|---|---|---|---|---|---|
| Full History | 1 stack | 481 | 59.01% | -21.18% | 4.09 |
| | 2 stacks | 905 | 55.00% | -10.90% | 5.60 |
| Primary v2 | 1 stack | 183 | 34.62% | -21.18% | 2.80 |
| | 2 stacks | 344 | 30.90% | -10.90% | 3.85 |
| Recent | 1 stack | 94 | 31.09% | -21.18% | 2.38 |
| | 2 stacks | 178 | 26.00% | -10.90% | 3.35 |
| 2026 YTD | 1 stack | 22 | 13.65% | -0.00% | 6.65 |
| | 2 stacks | 39 | 13.35% | -0.00% | 6.96 |

Consistent pattern in every preset: ~2x trades, ~half the drawdown, better
Sharpe — for 3-5 points less annualized return every time (splitting the pool
dilutes per-stack compounding efficiency). No preset flips this pattern.

**User's call**: 1 stack is the better fit. The capitulation ladder already
made the single-stack design "safe" without giving up return, so diluting into
2 stacks to buy *more* safety wasn't something to force — Grid Stacker Blended
was already meant to be a relatively safe stream that also wins big, not
optimized for max trade count at a return cost. Not adopted. Code kept
(opt-in, zero effect on default behavior) in case it's worth revisiting later.

## 3. Growing/dynamic stacks (tried, not adopted — underperformed)

User's original framing: start with 1 stack, add a new full-strength $100
stack every time the pool compounds past another whole $100 (not diluting
existing stacks). Given ~60%+ annualized returns, this was expected to reach
50+ concurrent stacks over 8 years.

Implemented via `stack_unit_size` param (each new stack always gets exactly
this much capital; capacity = `available_capital // stack_unit_size`, capped
at `max_stacks` as a safety bound only).

**Result: significantly underperformed.** Only ever reached 14 concurrent
stacks (not 50), and annualized return dropped hard: 38.48% vs. 62.65% for a
single stack, despite 1,658 trades (3.4x more than the single-stack's 482).

Root cause, confirmed with real numbers: `fear_dip` fired 2,463 times over the
8-year window; only 1,658 (67%) ever resulted in a stack opening — 805 signal
fires (33%) were wasted because nothing was free to catch them at that moment.
Each new stack needs its own fresh, distinct signal event to open (by design —
this is what preserves genuinely different entry timing/prices across stacks,
not just a bigger single position at one price). But stack *capacity* can jump
by several units at once from a single big compounding gain, while the *rate*
of fresh dip signals doesn't speed up to match — so a growing share of
compounded capital sits fully idle, earning nothing, waiting for enough
separate dip events to arrive. That idle-capital drag outweighs the benefit of
catching more entries.

Considered but not built: capping stack count at a modest number (e.g. 5) and
letting *each* stack's size keep scaling with the pool once at that cap
(instead of fixed $100/stack forever) — untested, would need real backtesting
before drawing conclusions. Moot for now since the fixed 1-stack design won
outright on the user's actual priorities (see section 2).

## 4. Profit-skim satellite BTC bucket (tried, not adopted — worsens overall return)

User's idea: instead of 100% cash-compounding, skim a % of every winning
trade's *realized gain* (not total capital) into a separate bucket. Once it
has ≥$10, buy BTC on a real dip (stricter than the main stream's signal — not
"at a recent high"). No market-based sell trigger: track a real cost basis,
and the moment unrealized value clears that cost basis by some premium, sell
*exactly* enough to recover the original principal as cash (back into the
bucket, waiting for the next dip) — the remainder becomes permanent "house
money," never sold again. Principal can never be lost twice; only realized
profit stays permanently exposed to BTC. Funded entirely by skims, never
draws from the main stream's own trading capital.

Implemented as `simulate_skim_bucket()` in `engine.py` — a post-process over
an already-computed `main_trades` DataFrame (not wired through
`run_backtest()`, since it's not a stream in its own right). Entry uses the
existing `drawdown_from_high` filter/indicator (price N% below its M-day
high — deliberately can't fire right after a fresh ATH). Also added
`dynamic_skim`: solves per-trade for the skim rate that would take
`target_trades` wins to accumulate `min_buy_capital` at the *current* main
stream pool size (naturally decays as the pool compounds — no separate decay
curve needed once floored, since trades-needed keeps shrinking from
compounding alone even at a fixed skim rate).

**Tuned via extensive backtesting**: entry 15% below 60-day high (tested
8/10/12/15/20% — 15% was the standout best, especially in the target Primary
v2 window; looser thresholds fired more often but didn't perform better).
Sell premium 50% (tested 10/20/25/50/100% — 50% was the most robust across
time horizons, best or near-best in every window, even though 100% edges it
out on the single longest, most-patient window). Dynamic skim: target 22
trades (not the user's illustrative 40 — solved against the *real* median
win, 1.8%, not the round 1% guess), floored 10%, capped 25%. This tuned
config genuinely fixed the problem it was built for — in Primary v2, first
buy moved a full year earlier (2024-03 → 2023-03), buys/recoveries roughly
doubled, and net gain flipped from -16.4% (a loss) to +40.2% (a real gain),
using a flat 10% skim as the baseline comparison.

**Then the user asked the two questions that killed it**, and they were the
right questions: (1) does taking a chunk of the main stream's compounding
capital actually help or hurt *overall* return, and (2) does this actually
solve the original idle-capital problem it was pitched to solve? Both no,
once tested honestly:

1. **Every prior test was internally inconsistent** — it skimmed off of
   `main_trades` that were generated assuming 100% of every gain compounds
   back (the original, unmodified backtest), when in reality skimming removes
   money from what compounds forward, which shrinks future position sizes,
   which shrinks future wins, which shrinks future skims — a real feedback
   loop that was never modeled. Corrected it using a valid shortcut (P&L
   scales linearly with capital deployed at entry, for a fixed price path, so
   the compounding trajectory can be replayed by scaling each trade's
   original pnl by the ratio of adjusted-pool/original-pool at that trade's
   entry — verified this holds throughout the ladder/tilt logic, all
   percentage-based and scale-invariant). Result: the compounding drag from
   skimming is severe — e.g. Full History, main stream alone would end at
   $8,491.15; with skimming active it only reaches $5,099.77, a **-$3,391
   drag** — while the bucket itself only grew the skimmed money to $810.94.
   **Combined total is *worse* than doing nothing, in every preset tested**
   (Full History -30.4%, Primary v2 -7.7%, Recent -7.6%, all vs. main-stream-
   only). Money removed early from a compounding pool costs far more than its
   face value — it costs everything it would have kept re-earning.
2. **The structural reason this can't be fixed by re-tuning the bucket**: the
   main stream already beats simple BTC buy-and-hold (that's the whole point
   of it as a strategy, and it's literally one of this project's four
   standard benchmarks per CLAUDE.md). The bucket, underneath its mechanics,
   is still fundamentally a buy-and-hold BTC position. So skimming moves
   capital from a strategy that beats buy-and-hold *into* buy-and-hold itself
   — moving money from the higher-yielding asset to the lower-yielding one.
   That's a structural drag on the source side, not a bucket-tuning problem —
   no adjustment to entry/exit/skim-rate fixes it as long as the main
   stream's edge over raw BTC holds (which is by design, the whole reason
   this project exists).
3. **It also never touched the original goal.** The idle-capital problem
   (~45% time-weighted utilization, ~55% idle — see the "capital utilization"
   discussion earlier in this session) is about capital sitting *reserved but
   unfilled* in slots 2-5, waiting on price triggers that may never fire.
   Skimming doesn't redeploy that idle reserve — it takes a slice of money
   that already **finished working** (a completed, realized win) and reroutes
   it elsewhere. Different problem, not solved by this mechanism at all.

Not adopted. `simulate_skim_bucket()` code kept (opt-in, zero effect on
default behavior, and the entry-timing/dynamic-skim design work inside it is
still sound in isolation) in case a version funded from something *other*
than the main stream's own compounding capital is ever worth revisiting.

## 5. Slot promotion / "impatience" trigger (adopt — part of GS: Reflex)

User's own idea, going after the same idle-capital problem as sections 2-4
from a genuinely different angle: instead of giving up on a stagnant slot
(rejected earlier) or running parallel capital elsewhere (sections 2-3,
rejected) or diverting realized profit (section 4, rejected), make the
position *impatient with itself*. Normal thresholds
(`cumulative_drop_pcts=[1,2,5,10]`) stay completely unchanged in the
fast-moving case. But each not-yet-filled slot gets a second, easier trigger
— the prior slot's own normal threshold — that only activates once the
position has sat open, unsold, past a given number of days without that
slot's normal trigger firing.

Implemented in `_run_blended_slots()` via two new position params:
```
"slot_promotion_days": [3, 6, 9, 12],       # per-slot unlock day, indexed like cumulative_drop_pcts
"max_promotions_per_position": 1,            # cap how many slots can jump the queue per position
"slot_promotion_anchor": "position_open",    # default; "prior_slot" tested and rejected (see below)
```
Promoted trigger for slot *k* = `cumulative_drop_pcts[k-2]` (slot 2's
promoted level is 0%, slot 1's own entry). Capitulation/the ladder are
unaffected — still measured off whatever price the last slot actually filled
at, promoted or not.

**Checked against the user's three specific criteria, tuned config
(`[3,6,9,12]` days, position-open anchor, max 1 promotion/position) vs. the
ladder+tilt baseline:**

| Preset | | Ann% | Max DD | Max days held | Time-weighted capital utilization |
|---|---|---|---|---|---|
| Full History | Baseline | 67.75% | -4.00% | 56.0 | 44.9% |
| | Promotion | **71.77%** | -4.00% | 57.0 | **47.5%** |
| Primary v2 | Baseline | 46.30% | -2.12% | 56.0 | 45.2% |
| | Promotion | **48.47%** | -3.08% | 57.0 | **47.9%** |
| Recent | Baseline | 53.95% | -2.12% | 56.0 | 46.9% |
| | Promotion | **57.42%** | -3.08% | 57.0 | **49.8%** |

1. **Annualized return**: improves in every window (+2 to +5 points).
2. **Max wait time (the 56-day figure, confirmed real)**: unaffected at
   this tuning (57.0 ≈ 56.0) — see below, this was NOT true before capping
   promotions per position.
3. **Capital utilization** (the *real*, time-weighted dollar metric — not
   the misleading "is a position open at all" one first reported by mistake,
   corrected mid-session): genuinely improves, ~45% → ~48-50% across all
   three windows. This is the metric that actually matters for the original
   goal, and it moved for real.

**Two real findings along the way, both important for whoever picks this
back up:**

- **Unlimited promotions per position is dangerous, not just imperfect.**
  Before capping, `[3,6,9,12]` let a single trade's entire remaining ladder
  cascade through all four promotions in sequence, pulling the blended
  average cost *up* (promoted fills are, by construction, worse prices than
  waiting for the real trigger) enough that one real trade's hold time
  roughly doubled (56 → 101.2 days) and Full History's max drawdown worsened
  (-4.00% → -5.20%). Capping at `max_promotions_per_position=1` fixes this
  almost completely (57.0 days, drawdown back near baseline) while barely
  giving up any of the return/utilization gain — confirmed a cap of 2 adds
  nothing over 1 (a second promotion in the same position essentially never
  happens even when allowed, so the risk only ever showed up when it was
  unbounded).
- **Anchor point matters, and `prior_slot` (resetting each slot's clock to
  the previous slot's actual fill time, instead of a fixed schedule from
  slot 1) is worse and, at its most aggressive setting, genuinely dangerous**
  — `prior_slot [2,2,2,2]` spiked Full History's max drawdown to **-28.05%**,
  far outside anything else tested this session. Mechanism: resetting the
  clock on every fill lets slots rapid-fire promote in quick succession
  during a real fast-moving crash — exactly the scenario the wide 1/2/5/10
  spacing exists to protect against. `position_open` anchor (the default,
  clock always measured from slot 1's original fill) doesn't have this
  failure mode, because it only ever engages for genuinely stagnant
  positions, not fast-moving ones. Swept both anchors against several day
  schedules; `position_open [3,6,9,12]` was the best or tied-best in every
  preset, not a lucky first guess.

**Confirmed adopted (2026-08-04)**: part of GS: Reflex's locked config
alongside the ladder and sentiment tilt. Full three-way combo backtested
against v8 across all four presets — see below.

## Full combo vs. v8 (Model 3 live), all four presets — v1

Capitulation ladder + sentiment tilt + slot promotion together, vs. v8
(`stream_config_id=37`):

| Preset | | Ann% | Max DD | Win Rate | Max Days Held | Utilization | Real Losses |
|---|---|---|---|---|---|---|---|
| Full History | v8 | 59.01% | -21.18% | 38.7% | 56.0 | 43.8% | 1 |
| | **GS: Reflex v1** | **71.77%** | **-4.00%** | 40.8% | 57.0 | **47.5%** | 4 |
| Primary v2 | v8 | 34.62% | -21.18% | 38.8% | 56.0 | 44.2% | 1 |
| | **GS: Reflex v1** | **48.47%** | **-3.08%** | 39.0% | 57.0 | **47.9%** | 2 |
| Recent | v8 | 31.09% | -21.18% | 40.4% | 56.0 | 45.9% | 1 |
| | **GS: Reflex v1** | **57.42%** | **-3.08%** | 37.2% | 57.0 | **49.8%** | 2 |
| 2026 YTD | v8 | 13.65% | -0.00% | 36.4% | 32.7 | 46.1% | 0 |
| | **GS: Reflex v1** | **25.72%** | -0.00% | 45.5% | 32.7 | 44.5% | 0 |

Return roughly doubles in three of four windows; max drawdown collapses from
-21.18% (v8's single hard capitulation line, entirely the one real Oct/Nov
2025 loss studied at length earlier in this branch's history) down to -3 to
-4% everywhere it ever mattered. Real losses go from 1 to 2-4 (still rare in
a ~480-trade population); utilization improves in three of four windows, dips
slightly in the thinnest one (2026 YTD, only 22 trades — likely noise).

## 6. Shallow breakeven margin (adopt — part of GS: Reflex v2)

Follow-on to v1: checking the actual trade log, **58-61% of GS: Reflex v1's
trades closed at EXACTLY $0.00 pnl**, not close-to-zero. Real mechanical
reason, not noise: the breakeven floor is defined as `avg_cost /
(1-taker_fee)` — the exact price where, after the exit-side fee, realized pnl
is precisely zero by construction. With a 4% arm + 5% trail, the trailing-
from-peak level doesn't actually become the *tighter* constraint than
breakeven until price has run to roughly +6.1% above cost — so any trade that
arms (crosses +4%) and reverses before ~+6.1% closes at exactly breakeven,
no matter how close it got (e.g. +5.9% still nets exactly $0).

First attempt — tightening `trailing_stop_pct` for shallow (1-3 slot)
positions — worked mechanically (flat trades in that range dropped to zero)
but cost real return in 2 of 3 windows (Primary v2 48.47%→38.68%, Recent
57.42%→34.41%), because a tighter trail also cuts off trades that would have
kept running further with the wider trail. No middle-ground trail % fixed
this — 2% was the *best* of the tightened options tested, and every step
back toward 5% just reintroduced flat trades without recovering return.

**Better fix: leave the 5% peak-trail completely untouched, and instead add
a small guaranteed margin directly to the breakeven floor itself** —
`breakeven *= (1 + shallow_breakeven_margin_pct/100)` for positions at or
under `shallow_slot_threshold` slots. This only changes the *specific*
dead-zone case (arms, then reverses before real profit locks in) from
exactly $0 to a small guaranteed real gain — trades that run far still get
the full, unmodified 5%-trail benefit. Swept 0.5-2.0% margin, applied first
to shallow slots (1-3) only, then to all 5 slots:

| Preset | Baseline | Margin 1.0%, slots 1-3 | **Margin 1.0%, all slots 1-5** |
|---|---|---|---|
| Full History | 71.77% | 78.62% | **95.49%** |
| Primary v2 | 48.47% | 51.95% | **64.16%** |
| Recent | 57.42% | 64.01% | **73.15%** |

Extending the margin to slots 4-5 too (not just "shallow" ones — original
assumption that deep/big-drop positions should be allowed to settle flat
turned out to be wrong, unsupported once actually tested) is a clean, large
additional win: **eliminates every remaining flat trade entirely** (0 across
every preset), no drawdown cost, no meaningful increase in real losses (same
loss count as the 1-3-only version — the margin converts flat exits to small
real gains on 4-5 slot positions too, without creating any new losses). 2.0%
margin was tempting in two windows but collapsed in Recent (38.35%, below
even baseline) — same red-flag pattern as other over-aggressive settings
found this session; 1.0% is the robust, consistent choice, beating baseline
in every single window tested. Trade count is not reduced by any of this —
if anything it ticks up slightly.

## Full combo vs. v8 (Model 3 live), all four presets — v2 (current)

`stream_config_id=38`, adds `shallow_breakeven_margin_pct=1.0` (all 5 slots)
on top of v1:

| Preset | | Trades | Ann% | Max DD | Win Rate | Flat | Real Losses |
|---|---|---|---|---|---|---|---|
| Full History | v8 | 481 | 59.01% | -21.18% | 38.7% | 294 | 1 |
| | **GS: Reflex v2** | 492 | **95.49%** | **-2.87%** | **98.8%** | **0** | 6 |
| Primary v2 | v8 | 183 | 34.62% | -21.18% | 38.8% | 111 | 1 |
| | **GS: Reflex v2** | 182 | **64.16%** | **-2.64%** | **97.8%** | **0** | 4 |
| Recent | v8 | 94 | 31.09% | -21.18% | 40.4% | 55 | 1 |
| | **GS: Reflex v2** | 96 | **73.15%** | **-2.12%** | **96.9%** | **0** | 3 |
| 2026 YTD | v8 | 22 | 13.65% | -0.00% | 36.4% | 14 | 0 |
| | **GS: Reflex v2** | 24 | **36.84%** | -0.30% | **95.8%** | **0** | 1 |

Win rate near-98% everywhere now (flat trades — the biggest chunk of v8's
"non-winners" — are gone, converted to real wins), return roughly 1.6-2.7x
v8's, drawdown still collapsed from v8's -21.18% single hard line down to
under -3% everywhere.

Full v2 config, in the same shape as the live `stream_configs` rows
(saved as `backtest.streams.stream_id=12` / `stream_configs.stream_config_id=38`,
version `v2`; linked into the placeholder Model 4 composition
`backtest.model_streams` at `lot_size_usd=100` so Stream Tester resolves the
real pool size):
```
"core_signal": "fear_dip", "core_params": {"dip_pct": 1.0}, "primary_timeframe": "4h",
"sentiment": {"fear_greed": {}},
"slots": {"slot_capital_weight": [20, 20, 20, 20, 20]},
"position": {
    "compound": true,
    "trailing_stop_pct": 5.0, "trail_arm_gain_pct": 4,
    "cumulative_drop_pcts": [1, 2, 5, 10], "entry_expiry_candles": 2,
    "capitulation_ladder_pcts": [20, 22, 24, 26, 28], "capitulation_ladder_final_cut_pct": 30,
    "sentiment_tilt": {"direction": -1, "strength": 0.4},
    "slot_promotion_days": [3, 6, 9, 12], "max_promotions_per_position": 1,
    "shallow_breakeven_margin_pct": 1.0, "shallow_slot_threshold": 5
}
```

## The Gauntlet — v2's robustness validation (run 2026-08-04)

Full 3-part suite (see memory `feedback_the_gauntlet.md` for the general
methodology, originated during Model 3's v8 pre-deploy QA):

1. **9-way single-year walk-forward** (2018 through 2026 YTD, each year run in
   isolation): 9/9 positive, 29.8%-177.5% annualized. 2018 — BTC's ~75%
   crypto-winter crash — posted the *highest* return of any year (111.6%),
   consistent with a dip-buying design profiting from volatility even in a
   falling market.
2. **Bootstrap distribution** (10,000 resamples with replacement, real
   trade-order shuffled, correctly modeling partial capital deployment per
   trade): real historical result ($31,586 from $100, Full History) sits at
   the **52.8th percentile** — essentially identical to Model 3's 51st
   percentile finding, genuinely middle-of-distribution, not a lucky outlier.
   **0% of 10,000 resampled paths lost money.** 5th-95th percentile range:
   $13,078-$86,336.
3. **Code review** of the full exit-priority chain (ladder marks → synthetic
   average → arm/breakeven+margin → trailing stop → capitulation gate →
   promotion's interaction with fill sequencing): no bugs found. Two notes,
   neither a problem: `shallow_trailing_stop_pct` (the rejected first attempt
   at the flat-trade fix, see below) is dead code, harmless but worth
   stripping in the cleanup pass; a "worst-case-within-candle" tie-break when
   capitulation and the trailing stop could theoretically both be touched in
   the same candle is a deliberately conservative modeling choice, moot in
   practice since the capitulation final cut has never fired once.

**Verdict: passes cleanly.** This is what actually earns deployment
confidence, not the headline annualized number alone.

## 7. Wide trail on deep winners / "let winners run" (tried, not adopted)

Idea: the 5% trailing stop is fixed regardless of how far above cost price
has climbed — a position 20%+ above its average gets cut on the same routine
5% pullback that would catch one that just barely armed. Tried widening the
trail (`wide_trail_gain_threshold_pct` / `wide_trail_pct` in
`_run_blended_slots()`) once a position clears a gain threshold, to let
genuinely strong moves run further before exiting.

**Result: worse in every single variant tested, every preset.** E.g.
threshold=10%/wide=10% dropped Full History from 95.49% to 78.82% ann *and*
worsened max drawdown from -2.87% to -5.77%. Root cause: this is a
mean-reversion strategy, not a trend-following one — the edge comes from
capturing a bounce back toward (and past) cost, and BTC's typical post-dip
move tends to relieve-rally then chop rather than run persistently. Giving
winners more room just gives back more of an already-captured gain when the
usual pullback arrives, instead of banking it.

Not adopted. Flagged as relevant to a *future trend-following stream*
though — the mechanic that fails here is exactly the one a genuine
momentum/breakout stream would want (see section 9).

## 8. Volatility-adaptive arm/trail (tried, not adopted for v2 — real finding, parked)

Idea: `trail_arm_gain_pct` (4%) and `trailing_stop_pct` (5%) are fixed
regardless of market conditions. Scaled both by a locked-in-at-entry ratio
(current ATR / its own recent average, clamped) — wider in violent regimes,
tighter in calm ones (`vol_adaptive_arm_trail` + `vol_adaptive_min_ratio` /
`vol_adaptive_max_ratio` in `_run_blended_slots()`; needs
`filters.atr_regime` present with a lax `max_pct_of_avg` to compute the
`atr`/`atr_avg` columns without actually gating entries).

**Real, notable finding: this is the only mechanism all session that broke
past the ~50% capital utilization ceiling** — jumping to 60-72% across every
preset (e.g. Recent: 49.0% → 63.7% at 0.8-1.3x clamp). Mechanism: sizing
patience to actual volatility means each position holds a bigger, more
efficient chunk of capital for longer. But it does this by taking **fewer**
trades (e.g. Full History 492 → 394), and return is a wash-to-slightly-worse
almost everywhere (Primary v2 is the one exception, +2pts). Given trade
count is an explicit priority, not adopted into v2.

**Parked for later**: this is the one lever that's actually proven capital
utilization isn't structurally capped at ~50% — worth revisiting if
"more capital working" ever outweighs "more trades" as the priority, or
combined with something that also drives trade frequency up.

## 9. Second stream exploration (parked — v2 alone is the plan for now)

Discussed building a genuinely complementary second stream for Model 4 (the
project's own "complementarity principle" — see `project_stream_design_philosophy`
memory — streams should cover different regimes, not be variations on the
same signal). GS: Reflex only ever fires on `fear_dip` (a price drop); it's
silent during a smooth uptrend with no real pullbacks.

**Candidate signal: `range_breakout`** (fires on a new high — the closest
thing to an opposite regime from "fires on a dip"). First-pass test, GS:
Reflex's exact tuned parameters just pointed at the new signal (zero tuning
for the new signal's actual risk shape):

| Preset | Trades | Ann% | Max DD | Win Rate |
|---|---|---|---|---|
| Full History | 171 | 33.07% | -5.07% | 95.3% |
| Primary v2 | 70 | 27.35% | -5.07% | 94.3% |
| Recent | 36 | 23.43% | -5.07% | 91.7% |
| 2026 YTD | 8 | 8.41% | -2.57% | 87.5% |

Concept validated (positive everywhere, no blowups) but nowhere near GS:
Reflex's level — expected, since none of the cascade spacing/ladder/tilt/
promotion/margin numbers have been re-tuned for a breakout-and-pullback risk
shape rather than a dip-and-recover one. Getting this to GS: Reflex's level
would mean redoing this whole session's tuning process for a new signal.

**Also checked**: no signal type in the codebase fires more often than
`fear_dip` (already the highest-frequency signal available — 2,463 raw fires
vs. the next-closest at 1,827). Hitting 2-3x trade frequency requires
loosening `fear_dip`'s own `dip_pct` (e.g. 0.15-0.2%), which isn't a
different regime at all — it would fire in largely the same conditions as
GS: Reflex, competing for the same capital rather than complementing it. Real
trade-off surfaced, not resolved: frequency vs. genuine complementarity.

**Also checked, out of curiosity, not as a real candidate**: slot 1 with
*zero* entry condition at all (buys immediately whenever free). Competitive
with `fear_dip` v2 — more trades everywhere, better on 2 of 4 presets, worse
on the other 2, drawdown stays controlled throughout. Notable as a sign of
how much of GS: Reflex's edge lives in the exit/risk machinery rather than
entry timing, but not an improvement — v2 (fear_dip) stays.

**Decision (2026-08-04): v2 alone, single stream, mirroring Model 3's
one-stream design.** Second-stream work parked, not abandoned — real next
step identified (tune `range_breakout` from scratch) whenever it's picked
back up.

## Next steps

- Named: **GS: Reflex** — capitulation ladder + sentiment tilt + slot
  promotion + shallow breakeven margin, all four adopted and composed
  together. **v2 is current** (config in section 6).
- **Saved to the local backtest DB**: `backtest.streams.stream_id=12`;
  `stream_configs.stream_config_id=37` (v1, ladder+tilt+promotion) and
  `=38` (v2, current — adds the breakeven margin), both linked into the
  placeholder Model 4 composition (`backtest.models.model_id=5`,
  `model_version=4`) via `backtest.model_streams` at `lot_size_usd=100` so
  Stream Tester resolves the real $100 pool for either version. Not yet run
  through the Stream Tester UI itself for visual review/chart confirmation —
  that's the natural next step, and it's a "run it and look at it" step for
  the user in Streamlit, not something to do headless.
- Once visually confirmed in Stream Tester, `save_stream_test()` records the
  run (per-preset), completing the "lock a stream config" step in the model
  lifecycle (CLAUDE.md) — separate from formally assembling a *model*
  composition (a real model-level backtest, not just the placeholder), which
  is the step after that, whenever a full Model 4 is ready to move past a
  single stream.
- **Passed The Gauntlet (2026-08-04)** — see the dedicated section above.
  Genuinely earned confidence, not just a good headline number.
- **Clean production cutover (2026-08-05)**: concurrent-stacks,
  growing-stacks, skim-bucket, wide-trail, vol-adaptive, the `prior_slot`
  promotion anchor, and the capitulation ladder's start/step shorthand were
  all removed from `_run_blended_slots()`/`engine.py` — confirmed via an
  independent hand-traced math check plus a full re-run of every preset,
  byte-identical to the numbers above and to Model 3 (v8)'s numbers, before
  and after. See ADR 007's summary at the top of this document.

### Explicit plan for next session (2026-08-04, user's own words)

Stay on this branch (`experiment/model4-design`). In order:
1. **Write the CLEAN production code** for GS: Reflex v2 — this branch's
   `engine.py` has every rejected experiment (sections 2-4, 7-8) still sitting
   in it as dead/unused params. Strip everything not part of v2's actual
   config down to something as clean as `_run_blended_slots()` was before
   this session, plus only the mechanisms GS: Reflex v2 actually uses (ladder,
   sentiment tilt, promotion, breakeven margin).
2. **QA hard — smoke tests, bug hunt.** Not just re-confirming the numbers
   (already Gauntlet-validated) — specifically re-verify the *cleaned* code
   reproduces the exact same v2 numbers byte-for-byte after stripping the
   unused paths, since a cleanup pass is exactly where a real regression
   could sneak in unnoticed.
3. **Clean code, clean code, clean code** — explicitly repeated 3x by the
   user, i.e. don't treat this as optional polish.
4. **Get everything set up for Model 4 to be finalized** and ready to become
   the next real working branch, targeting September (per the tournament's
   2-3 month cadence and the user's own stated no-earlier-than-Sep-1
   timeline from the original capitulation-loss discussion).
