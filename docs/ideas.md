# Ideas — things we want to try, or come back to

Not a decision log (see `docs/decisions/` for those — an idea graduates there
once it's actually being built or has a real go/no-go verdict). This is the
running list of stuff worth hashing out: some parked after a real attempt,
some brand new and untested. Numbered so we can reference and track them;
numbers are permanent once assigned — don't renumber on reorder, just append.

Status values: `raw` (pitched, not investigated) · `parked` (tried, didn't
work, reason known — worth revisiting if the blocker gets solved) ·
`gated` (parked behind a specific prerequisite, revisit once that's done) ·
`active` (currently being built/tested) · `tested — dead end` (genuinely
tested, not just reasoned about; conclusion is "don't pursue further"
absent a real new angle) · `promoted` (became an ADR).

---

## 1. Higher trade frequency

**Status:** gated — behind #3 (execution-layer fee reduction). Never got a fair fight; the real blocker is fees, not the signal.

**2026-08-31 reassessment (after closing #2 as a tested dead end):** deliberately kept open, not closed — this is a materially weaker "no" than #2's. The only real evidence against it is fee math (real 1.20% round-trip) and one abandoned experiment (Quiet Climber v3: good backtest, bad live-adjacent behavior in an actual 2026 correction) — not "every tested variant loses money regardless of tuning," which is what actually killed #2. Don't test this again until #3 has moved the real per-trade cost down; testing it against today's fee structure would just reproduce the same fee-drag conclusion that shelved it originally.

Tried once adjacent to this: Quiet Climber v3 loosened its trail for more
frequent fires, got strong backtest numbers (23.7% ann, Primary v2), but
badly whipsawed in the 2026 correction and wasn't pursued past v4. The
harder constraint predates any stream attempt: fees were assumed 0.50%
round-trip when frequency was first ruled out (`docs/decisions/001`,
`docs/decisions/006`), later corrected to a real 1.20% (maker+taker,
confirmed live) — which makes the frequency question even harder under the
original framing.

**Why I don't think this is dead:** the lever isn't "trade more," it's
"make each trade cheaper," which reopens frequency as viable:
- Maker-fill optimization on entries — 0.40% maker vs 0.80% taker is the
  whole game. A limit order that reliably fills at maker vs. one that
  crosses the spread and pays taker changes round-trip cost by 2x.
- Kraken's fee tier is volume-based over a trailing 30 days — deliberately
  climbing tiers could compound into materially lower fees over time,
  independent of any single stream's edge.
- Only once one or both of those move the real fee number down is it worth
  re-testing a frequency-oriented stream honestly.

**Next step if revisited:** quantify current maker-fill rate on live orders,
see how much room there actually is before touching signal design at all.

---

## 2. Cascade / pyramid-down DCA slots ("shouldn't lose, worst case average down")

**Status:** tested — dead end (2026-08-31). Built and genuinely tested
twice now, months apart, by two different approaches, converging on the
same conclusion. Not closing this because we got tired of it — closing it
because the evidence is unusually consistent for a "no."

Model 3 (Grid Stacker Blended) backtested at 84.77% ann and went live
2026-08-01. A phantom-fill bug in the backtest (credited fills the market
never actually touched — one-sided candle-range check) had been inflating
results 10-20x for blended mode's whole life. Live-replay against real
history told the truth: 49% loss rate, capital permanently frozen at
$49.42 by Aug 2022. Loss rate scaled directly with slot depth — 0% at 1
slot, 100% at 5 slots. **The cascade mechanic itself was the failure mode.**

Root cause: the "never sell at a loss" floor was enforced with an
unconditional market sell the instant it armed — which can fill *below*
that floor during the fast crash the design exists to survive. Fixed
version (resting limit-sell at the floor, proper fee/timing accounting)
was honest but weak: Model 3 Recent window went to -1.8% ann (a real
loss); a dedicated redesign (GS: Phoenix, `experiment/model3-4-redesign`
branch, never merged) tried five variants and **none were net positive**
on the full Primary v2 window. Model 3 was sold out live for a small real
loss and archived; Model 4 never went live.

**Why I don't think this is dead:** the floor-guarantee problem is
specific and fixable in principle — the exit mechanism needs to either
never let price cross the floor without filling (resting limit that just
doesn't fill in a gap-down, accepting the position rides past floor
briefly rather than realizing below it) or accept a probabilistic floor
instead of a guaranteed one and price that into position sizing. GS:
Phoenix retuned parameters without changing that mechanism — that's
probably why it still failed.

**Next step if revisited:** redesign the floor-exit mechanism first, not
the ladder spacing or slot count. Test the mechanism in isolation
(single-slot, does the floor actually hold?) before scaling to 5 slots.

**2026-08-31 rebuild, from scratch, addressing the floor-exit problem
head-on:** revisited per the "next step" above — this time with a real,
accepted stop-loss (slot 5 can genuinely lose, no guaranteed floor),
graduated per-slot exit targets ("win big on slot 1, hedge more on each
slot after"), volatility-adaptive entry spacing, and eventually a full
market-character scoring system (dip/trend/breakout/volume signals,
reusing the real ingredients from Momentum Rider/Dip Hunter/Breakout
Scout/Volume Raider) driving both entry and exit continuously. Tested
against real 2021-2024 data (BTC bull, bear, chop, bull), non-compounding
$20/slot basis:
- Plain fixed-percentage ladder: -$47.92 over the 4 years.
- Adaptive entry spacing alone (volatility-scaled, fixed exit targets):
  **-$15.04 — the best result of the entire session**, still a loss but
  meaningfully smaller than the fixed baseline.
- Every other variant tested — adaptive exit-scaling, both adaptive
  together, the full market-character system, and a real 144→16-combo
  coefficient search over that system's weights — **landed worse than
  the plain fixed baseline**, most far worse (as bad as -$146 in the
  weakest tested combo).

**Root cause of why "smarter" made it worse, every time:** any mechanism
that made the system more willing to average down (higher conviction →
smaller required drop, wider exit ambition, etc.) increased how often a
cascade built all the way to slot 5 — and slot 5's backstop loss is where
essentially all the damage lives, in every version tested. The value of
better entry timing was consistently smaller than the cost of the extra
deep-cascade exposure it created. Making the system MORE cautious always
helped; making it more eager never did, no matter how the eagerness was
justified.

**Verdict:** this isn't one failed attempt, it's two independent ones —
the original Model 3/4/Phoenix effort (five tuned variants, all net
negative, killed by the market-order floor-exit gap) and this session's
from-scratch rebuild (which fixed that exact gap and still lost, for a
structurally different but equally consistent reason). A fixed-depth
cascade is a bet that BTC reliably mean-reverts within a bounded window;
that bet works in a fast V-shaped recovery and loses in a genuine
sustained decline, which BTC has produced roughly 1 year in 4 in the
data available (2018, 2022). Not calling this permanently impossible —
but "smarter timing" was the obvious next thing to try, we tried several
real versions of it, and it made things worse every time. Don't revisit
without a genuinely new angle on bounding the downside, not just another
tuning pass.

---

## 3. Execution-layer fee reduction

**Status:** raw

Doesn't require a new stream — changes the cost basis every existing
stream runs on. Two parts:
1. Maker-fill optimization: how often do live limit entries actually fill
   maker vs. cross the spread and pay taker? If there's room to improve
   placement (slightly inside the spread, adaptive to recent volatility)
   without materially hurting fill probability, that's free money across
   every model.
2. Fee-tier climbing: model whether deliberately increasing 30-day volume
   (more/larger trades, or even wash-neutral volume if that's within
   Kraken's ToS — needs checking) pays for itself once the tier drops.

**Next step:** pull real maker/taker fill rate from `live.lots` order
history across Model 1 and Model 2 to see if there's actually a gap worth
closing before building anything.

---

## 4. Deterministic regime classifier as a gate

**Status:** raw

Not a new stream — a frozen, offline-trained classical ML model (gradient-
boosted tree, or even just ADX/ATR thresholds) that labels the current
regime (trending vs. mean-reverting vs. chop) and gates which existing
streams are allowed to fire. Trained once during the build phase, frozen
before deployment — stays inside the "no LLM in live execution path" rule
(`docs/decisions/002`) because it's a fixed function once trained, not a
live model call. Ties into the complementarity principle already in
`project_stream_design_philosophy` — this would make regime-awareness
explicit instead of implicit in each stream's own filters.

**Next step:** define regime labels precisely enough to backtest against
(what counts as "trending"?), then check whether gating existing streams
by regime actually changes the Gauntlet numbers before adding an ML step
at all — a simple ADX threshold might get 80% of the value with none of
the training/overfitting risk.

---

## 5. Time-of-day / day-of-week liquidity filter

**Status:** raw

BTC has documented thin-liquidity windows (weekends, certain UTC hours)
where slippage and fake-outs are more common. A deterministic filter that
simply avoids entries during known-thin windows could reduce slippage
without touching any stream's signal logic. Cheap to test — pure backtest
question, no new infrastructure.

**Next step:** check whether Kraken BTC/USD actually shows a measurable
liquidity/slippage pattern by hour/day in our own market_data, before
assuming the commonly-cited pattern holds at our trade size.

---

## 6. Staged / partial exits

**Status:** raw

Scale out of a position in tranches (e.g., sell 1/3 at first trailing-stop
arm, let the rest ride a wider trail) instead of one all-or-nothing exit.
Changes the risk/reward shape without touching entry signals. Interacts
with the "trailing stops over fixed targets" decision (`docs/decisions/003`)
— worth checking that partial exits don't contradict the reasoning there
before building.

**Next step:** re-read `docs/decisions/003` for why fixed targets were
rejected, make sure partial exits don't reintroduce the same problem in a
different shape.

---

## 7. Trailing-stop win/loss distribution audit — is tightening the trail costing us on a handful of big trades?

**Status:** closed 2026-08-28 — tested against Volume Raider (highest-giveback stream), both candidate mechanisms hurt return. Not pursued further; see final verdict below.

Hunch: adjusting trail % has historically hurt performance, but maybe only
because of a small number of large trades where a tighter trail cut off a
big run early — not because the trail setting is wrong on the median trade.
If most trades are roughly a wash either way and the damage is concentrated
in a handful of outliers, the framing changes: it's not "what's the optimal
trail %" but "should we treat big winners differently than typical trades"
(ties into #6, staged exits, and possibly a wider trail that only kicks in
once a trade is already up big).

**What to actually check**, per stream, across the Primary v2 window:
- Distribution of exit reasons (trailing stop hit vs. other) and the P&L
  at each exit — full histogram, not just the aggregate return.
- For trades where a *tighter* trail setting was tested: how many indi­
  vidual trades flipped from win to loss (or shrank) vs. how much of the
  aggregate return delta is explained by just the top 3-5 trades?
- Specifically test the framing in the question: would locking in a
  guaranteed ~15-20% band on the biggest runs (instead of trailing them
  all the way down) have produced a better realized outcome than the
  current trail settings, on this specific historical window? Watch for
  survivorship/overfitting risk — "would have worked on this window" needs
  walk-forward or out-of-window checking before it changes anything live.

**Next step:** pull per-trade exit data (stream, entry, exit, exit_reason,
realized P&L) for the current live stream configs across Primary v2 from
`backtest.stream_tests`/`reporting.all_lots`, build the distribution, then
decide if it's worth a follow-up backtest variant.

**Priority:** one of the first ideas to actually do.

**Concrete mechanism to test as part of this audit — "leading_sell" /
profit-lock exit** (pitched 2026-08-28, prompted by watching a stream sit
25%+ unrealized during the recent ~80k move and give a chunk of it back
before the trailing stop caught it): once unrealized gain crosses a
threshold, do something other than wait for the normal trail — also frees
the slot to redeploy sooner, which is a second real benefit independent of
whether it improves realized P&L. **Note: this directly challenges
`docs/decisions/003` (trailing stops over fixed targets)** — that ADR's
argument is about capping *typical* winners early; this is aimed only at
the *tail* (outlier gains), which is a different claim, but reopening it
should be explicit, not incidental. Three candidate shapes, most to least
consistent with ADR 003's "let the market decide" reasoning:
1. **Ratcheting trail** — trail tightens once gain crosses the threshold
   (e.g. 4% trail → 1.5% trail past +20%). Still market-decides, just
   decides faster once already an outlier.
2. **Partial leading_sell** — sell a portion at the threshold, let the
   rest keep riding the normal trail. Same mechanism as #6.
3. **Hard leading_sell** — full exit at the threshold, no exceptions.
   Closest to the original pitch; most directly reopens ADR 003.
Which (if any) actually helps is exactly what the win/loss distribution
audit above should answer — don't build any of these off one memorable
trade, check whether the pattern holds across the full window first.

**Audit result, corrected (first pass 2026-08-28 was wrong — see below;
corrected same day):** ran fresh through `run_backtest()` directly for
all 7 stream configs actually composing Model 1 and Model 2 (Primary v2,
177 trades total), rather than trusting `backtest.lots.high_water_mark`.
The original hunch holds: giveback is real and substantial on big
winners, tracking roughly each stream's configured trail (7-10%) plus
overshoot from candle-close gaps past the stop —

| Exit reason | n | avg giveback | max giveback |
|---|---|---|---|
| trailing_stop | 130 | 10.46pp | 17.85pp |
| stop_loss | 17 | 6.78pp | 17.85pp |
| max_hold | 27 | 3.58pp | 7.97pp |

Volume Raider peaked at +76.4%, realized +58.8% (17.6pp given back);
Momentum Rider peaked at +66.0%, realized +52.7% (13.3pp given back). 19
trades peaked above +25%. Top-10 winners are still 43% of total profit —
real concentration, and now confirmed real giveback on those specific
trades too. **leading_sell / ratcheting-trail is a live, worth-pursuing
question**, not settled — same three candidate shapes as above apply.

**Data bug found and NOT yet fixed, uncovered while running this audit:**
`backtest.lots.high_water_mark` is silently wrong for every model-level
test generated via the live-replay path (`run_live_replay_stream` —
used for Model 1/2's model_tests 151 & 156, and any other `single`/
`staggered` model-level test). That function never tracks a real
intra-trade peak, only entry/exit; something downstream defaults the
missing value to `exit_price` instead of leaving it null, so the column
reads as if every trade closed exactly at its peak (0% giveback on 172/172
closed trades checked). **This is a diagnostic-column bug, not a P&L bug**
— `entry_price`/`exit_price`/`realized_pnl` are untouched, so Model 1/2's
trusted 13.6%/15.90% ann figures are NOT affected. But it silently breaks
anything that reads `high_water_mark` for these rows — MAE/MFE-style
charts, this audit itself on first pass, any future peak-based analysis.
**Needs a real fix**: either have `run_live_replay_stream` track a true
running peak, or have `_save_lots` leave the field null instead of
defaulting it. Filed here rather than as its own numbered idea since it's
a bug, not a feature idea — worth a short follow-up session.

**Open follow-up, not yet run:** this only tests the *current* trail %
values. It doesn't test whether a looser trail (tuned for "let it run
further") would find bigger peaks the current tight trail never reaches
in the first place — that's the actual ADR 003 tradeoff and would need a
fresh backtest variant with different trail parameters, not just
re-measuring existing trades.

**Mechanism testing, both closed 2026-08-28 (Volume Raider, Primary v2 —
picked as the test case because it had the highest average giveback of
the four streams):**

*Ratcheting trail* (`trailing_stop_steps`, already live-validated code in
`position_monitor.py` — no new build needed): tested flat 10% baseline
against `[[20,5]]`, `[[20,4]]`, `[[20,6],[40,3]]`, and `[[40,4]]` via
`run_live_replay_stream`. **Every variant underperformed baseline** —
best case (peak-only 40%→4%) still lost 2.2pp of annualized return
(23.43%→21.20%), worst case lost over 3pp. Trade count rose in every
variant (38→40-43), meaning the tighter trail causes more early-exit +
re-entry cycling, and for a bursty momentum stream like this some of
those early exits cut off real continuations rather than just trimming
fat.

*Hard leading_sell* (`take_profit_pct` — exploratory only, no live wiring
exists yet, tested via the fast raw engine not the live-validated
replay path): tested flat 10% baseline against a 20%/25%/30% hard cap.
**All three caps cut annualized return substantially** — worst at 20%
(20.73%→13.40%, over a third of return gone), least bad at 30%
(→17.87%, still down ~14%). Trade count did rise (39→44, confirming
"redeploy capital faster" works as expected), but only 7-12 trades per
variant actually hit the cap, and those are exactly the trades that
would otherwise have run to 40-76% under the trailing stop — capping
them sacrifices far more than the extra trades gain back.

**Verdict:** both mechanisms fail for the same reason — Volume Raider's
return is concentrated in a small number of huge runs (top 10 winners =
43% of total profit), and anything that caps or tightens gains on the
way up sacrifices more from those specific trades than it recovers
elsewhere. Real giveback (measured earlier) is closer to "cost of
admission" for catching the big run than free money left on the table.
Not tested against Momentum Rider/Breakout Scout/Dip Hunter — could
revisit per-stream if one of those turns out to have a flatter return
distribution (less concentrated in outliers) than Volume Raider, but
deprioritized for now. ADR 003's original reasoning (let winners run,
don't cap them) holds up empirically here, not just as a stated
principle.

<details><summary>Original (wrong) first-pass result, kept for the record</summary>

First pass measured giveback directly from `backtest.lots` for model_test_id
151 & 156 and found ~1.2-1.7pp average giveback regardless of trade size —
this looked like the hunch didn't hold. That result was an artifact of the
`high_water_mark` data bug described above (every row showed 0% giveback
at the source), not a real finding. Caught by cross-checking against the
live Model 1 dashboard export, which showed 7-10% trail distances on open
positions — inconsistent with a ~1.5pp measured giveback on closed ones.

</details>

---

## 8. Adversarial/invariant testing for the recurring "silent failure" bug class

**Status:** raw

Looking back at every real live-money bug found so far, they cluster into
one pattern: not a wrong formula, but a **silent failure or an incomplete
check** that looked fine until specifically audited against real data:
- `get_order_status()` silently kept only the first fill of a multi-trade
  order, under-recording a real position with no exit path (2026-08-17,
  see git log `bc3ee96`).
- The blended-mode phantom-fill bug: exit-fill check was one-sided (only
  checked `low`, not `high`), crediting fills the market never touched —
  inflated backtest results 10-20x for the cascade mode's entire life
  before live-replay caught it (idea #2's root cause).
- `check_fee_drift()` needs a real Kraken API call but no healthcheck
  workflow ever mapped the API credentials into the job env — the
  function's except-path logged and returned `True` (as if fees matched)
  instead of raising, so it verified nothing for as long as it ran.
- Recurring cross-model isolation gaps: hardcoded `MODEL_LABELS`/`id=1`
  assumptions, unscoped `live.streams`/`live.lots` queries missing
  `model_id` filters (currently mid-fix in `2_live_monitor.py` as of
  2026-08-28) — each one only found by someone happening to notice the
  dashboard looked wrong.
- Missing signal-type display branches in Live Monitor silently mislabeling
  readiness instead of erroring (`feedback_live_monitor_signal_branches`
  — 4 of 9 signal types still unimplemented, latent).

None of these were caught by existing tests because the tests that exist
mostly check "does the happy path compute the right number," not "does
this fail loudly when an assumption breaks" or "is every model-scoped
query actually scoped." Concrete, scoped ideas (not one big initiative):

- **A repeatable static check** (grep-based or AST-based, doesn't need to
  be fancy) that flags any query against `live.streams`/`live.lots`/
  `live.models`/`live.executor_state` without a `model_id`/`WHERE`
  filter — turns "found by accident" into "caught before merge." Cheapest
  version: a pre-commit or CI grep for those table names without a nearby
  `model_id`.
- **Fail-loud audit**: grep every `except` block in `src/live/` for ones
  that log-and-return-success instead of raising/alerting — the fee-drift
  bug's exact shape. `notifier.alert_order_failed` already exists as the
  pattern to extend.
- **Multi-trade fill property test**: the class of bug `get_order_status()`
  had — a property/fuzz test that simulates an order splitting into N
  trades (N=1..5, varying vol/cost/fee splits) and asserts the aggregate
  always matches, instead of only the 4 fixed-scenario tests that exist
  now in `tests/live/test_kraken_client.py`.
- **Automated reconciliation invariant**: turn the manual "sum tracked BTC
  across open lots and compare to real Kraken balance" check (done by
  hand after both the 2026-08-05 and 2026-08-17 incidents) into a
  scheduled healthcheck assertion instead of something only run after a
  bug is already suspected.
- **Adversarial code-review pass**: run `/code-review` (or a dedicated
  pass) specifically briefed on this bug class — "find silent failures,
  incomplete scoping, and one-sided range/boundary checks" — rather than
  a generic review, on `src/live/` and the backtest exit-fill logic.

**Next step:** pick the cheapest one first — the model_id-scoping static
check — since it would have caught the bug currently sitting uncommitted
in `2_live_monitor.py` and the pattern has recurred multiple times.
