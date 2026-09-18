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

---

## 9. Other crypto assets (ETH, SOL, DOGE) as new Models

**Status:** raw

Pitched 2026-09-14: apply the existing stream techniques (volume surge,
RSI recovery, breakout, EMA crossover) to other Kraken-listed assets
instead of only BTC/USD. Not a parameter tweak on an existing stream —
each asset has its own volatility profile, so thresholds/trails would
need re-tuning per asset, and each needs its own `market_data` ingestion
and full backtest history before anything is trustworthy.

**Real concern going in:** during systemic crypto drawdowns, alts tend to
move *with* BTC (often higher beta, i.e. harder), so a second crypto
Model may not be genuine diversification — closer to "more BTC-correlated
exposure" than an independent bet. Worth checking actual historical
correlation (BTC vs. candidate asset, especially during 2022-style
drawdowns) before assuming this adds anything the tournament doesn't
already have via Model 1/2.

Ranked by how promising this looks before any real testing:
- **ETH** — best candidate. Deep Kraken liquidity, long clean price
  history, moderate volatility (lower than BTC, still meaningfully
  higher than equities) — the existing mechanical approach probably
  transfers with retuning, not a redesign.
- **SOL** — plausible but noisier; shorter clean history, higher vol cuts
  both ways (bigger wins, bigger stop-outs, more whipsaw risk).
- **DOGE** — weakest candidate. Price action looks more driven by
  social/narrative spikes than the technical patterns these streams key
  off (volume surges, RSI recovery, breakouts), and Kraken liquidity/
  spread at this trade size is worse than BTC/ETH. Technical edge here
  is unproven and could easily be near-zero.

**Next step:** pull real BTC-vs-candidate-asset correlation from
Kraken's own OHLCV history (start with ETH, since it's the strongest
candidate) — confirm there's an actual diversification case before
building a new `market_data` pipeline and backtesting anything.

---

## 10. Applying these techniques to equities / an index ETF (e.g. VOO)

**Status:** raw

Pitched 2026-09-14, from a hunch that an index ETF would be "easier to
win" than BTC because it's more predictable. **Working assessment: this
is probably backwards for this specific technique**, not an easy win —
worth writing down clearly since the intuition and the likely reality
point opposite directions.

Three concerns, independent of each other:
1. **Volatility mismatch** — the whole mechanism (trailing-stop
   volatility harvesting, dip-buy mean reversion) depends on frequent,
   meaningful price swings landing inside the round-trip fee cost. BTC's
   daily vol regularly runs 3-5%+; a broad index ETF like VOO is
   engineered to be *less* choppy (that's the point of diversification),
   often under ~1%. Lower vol means fewer trades clear the fee hurdle at
   all, not more winning trades.
2. **PDT rule (FINRA)** — a margin account under $25k gets flagged as a
   Pattern Day Trader after 3 day-trades within 5 rolling business days.
   A $100 account cannot run anything like this system's trade cadence
   on U.S. equities without immediately tripping that restriction — this
   is a hard regulatory blocker, not a tuning problem.
3. **No 24/7 market** — U.S. equity markets run ~6.5 hours/day, 5 days/
   week. The "autonomous, always-on, zero human intervention" design
   assumption this whole project is built around doesn't hold for
   equities the way it does for crypto.

If the real interest is "index funds feel safer/more predictable," that
reads as a case for plain buy-and-hold (which by definition tracks the
S&P benchmark this project already compares against), not a variant of
the active volatility-harvesting system built here.

**Next step:** none planned — flagged as a likely dead-end-on-arrival
for this technique specifically, kept here rather than acted on. Would
need the PDT-rule blocker resolved (e.g., a cash account with T+1/T+2
settlement instead of margin, at a much lower trade frequency) before
it's even worth a backtest.

---

## 11. Time-since-HWM stagnation trigger ("bars since new high") for trail tightening

**Status:** raw

Pitched 2026-09-14, prompted by watching all 4 currently-open live lots
(Model 1 + Model 2) sit at the same high-water mark ($81,746.90) for
~10 days with price refusing to make a new high. Question: once a
position's HWM stalls for a while, is that itself a signal worth acting
on — independent of how far price has actually pulled back from the
peak?

This is a real, named technique outside this project — usually called a
"time stop" or "stagnation exit" in trend-following/CTA systems: if price
fails to make a new high within N periods after a run, that's read as
fading momentum, distinct from a price-based trailing stop. **Confirmed
we don't have it at all** — neither as a live mechanism nor as tracked
data. `live.lots.high_water_mark` stores only the value, not a timestamp
of when it was last updated, so "days since HWM" isn't even queryable
today without a schema change or a derived lookup against `market_data`
for the last candle where close ≥ current HWM.

**Why this might avoid the failure mode that killed the mechanisms tested
in #7:** ratcheting-trail and hard-profit-lock (both tested, both worse
than baseline) are *gain-triggered* — they tighten/cap uniformly once a
trade is up big, which is exactly why they hurt Volume Raider specifically
(its return is concentrated in a handful of huge runs that need room to
keep going). A stagnation trigger is different in kind: it only fires when
price actually stalls, so a fast-moving runner that keeps printing new
highs never trips it, while a trade that's gone dead sideways after a big
run gets tightened protection. Untested hypothesis, not a conclusion —
could easily fail for a different reason (see risk below).

**Real risk, same shape as every other tightening idea tried so far:**
consolidation near a high after a strong run is also exactly what healthy
continuation looks like before it resumes. A stagnation stop tuned too
aggressively would cut positions right before they run again — the same
whipsaw risk every trail-tightening mechanism carries, just clocked
instead of priced.

**Next step:** backtest a "bars since new high" trigger against the four
stream configs actually composing Model 1/2 (Primary v2 + the real bear
windows from the Gauntlet), varying the stagnation window and the
tightening amount, the same way the #7 mechanisms were tested via
`run_live_replay_stream`. Needs the stream-locking adversarial-code-review
gate before trusting any promising number, same as any other engine
change — see [[feedback_model_finalization_adversarial_gate]].

**Explicitly not doing:** any manual/discretionary adjustment to the
currently open live positions based on this hunch — deterministic rules
only, no ad hoc live intervention; this has to earn its place through a
real backtest first.

---

## 12. Confluence + ATR-adaptive next-gen stream candidate

**Status:** raw

Pitched 2026-09-14 after a full stock-take of the 7 live stream configs
(Model 1: MR v2/DH v2/BS v2; Model 2: VR v1/DH v3/BS v3/MR v4) against
everything actually implemented in `indicators.py`/`signals.py`/
`engine.py`. Every live stream shares the same shape — one `core_signal`,
2-4 filters, a flat `trailing_stop_pct`, single slot — this is an attempt
to combine real, already-implemented-but-unused pieces plus one genuinely
new mechanism, rather than tune an existing stream further.

**Built from:**
- **2-slot `scale_up` capital shape** — not new; VR v2/v3/v4
  (`stream_configs` 19-21, exploration only) already backtested at
  **+31.0% Primary v2, +34.5% Recent, +30.9% Full History** — this is the
  strongest real evidence that 25-30%+ is reachable, sitting in the DB
  already. Excluded from Model 2 only for 2026 YTD weakness (-13% to
  -35%, a real bear-market issue) and a preference for a "cleaner"
  4-single-slot composition — not because the mechanism failed.
- **MACD crossover core signal** — implemented in `indicators.py`/
  `signals.py`, zero live streams use it. Different momentum character
  than MR's EMA crossover.
- **ATR-scaled trailing stop** — genuinely new. Every live stream uses one
  static `trailing_stop_pct` regardless of current volatility regime. A
  trail sized to `k × ATR` at entry (recomputed per trade) adapts to the
  market's actual current character instead of one flat number for calm
  and violent conditions alike. Mechanically distinct from
  `trailing_stop_steps` (the gain-triggered ratchet already tested and
  killed for Volume Raider in idea #7) — this reacts to volatility, not
  to how much the trade is already up.
- **Regime-conditional ADX gate**, not blanket-always-on. ADX was tested
  once (MR v3, Primary v2 — a bull-heavy window) and rejected for costing
  -2.8% ann there, but the same test halved drawdown in choppier
  regimes. Only ever tested as an always-on filter; gating it behind a
  simple regime check (e.g. 200-SMA slope) so it only activates in
  choppy conditions has never been tried. Ties directly to idea #4.

**Also ties to:** idea #6 (staged/partial exits — the ATR-adaptive trail
could pair with scaling out in tranches instead of one all-or-nothing
exit) and idea #11 (stagnation trigger — a natural second layer on top
of an ATR-scaled trail).

**Honest calibration:** 20-25% sustained looks credible from (scale_up
shape) + (regime-conditional ADX) + (ATR-adaptive trail) alone, since two
of the three pieces already have real backtest evidence behind them.
30%+ is plausible in strong bull windows (already seen in the VR
scale_up numbers above) but the same bear-window drag that excluded VR
v2-4 from Model 2 is the real risk to sustaining it across regimes, not
just favorable ones — this still needs a genuinely bear-hardened
complement, per [[project_stream_design_philosophy]]'s complementarity
principle, not a assumption that one strong stream alone gets there.

**Next step:** build one piece at a time via the normal stream workflow
([[feedback_stream_workflow]]) — start with the ATR-adaptive trail alone
against an existing locked config (cheapest, most isolated test of the
one truly new mechanism) before combining with MACD or the scale_up
shape. Needs the stream-locking adversarial-code-review gate
([[feedback_model_finalization_adversarial_gate]]) before trusting any
promising number, same as any other engine change.

**ATR-adaptive trail piece, built and tested against Volume Raider
(2026-09-14/15):** ported `trailing_stop_atr_multiplier` (already
implemented in `engine.py` but never live-validated) into
`position_monitor.py` and the live-replay path — real plumbing, no
schema change needed (ATR recomputed fresh each tick, same as current
price already is, not frozen at entry). Tested multipliers 2x/3x/4x/6x/8x
against VR's real Primary v2 baseline (flat 10%, +23.4% ann, 38 trades):

| Variant | Trades | Ann. | Max DD |
|---|---|---|---|
| Baseline (flat 10%) | 38 | **+23.4%** | -20.7% |
| ATR x2 | 91 | -17.0% | -63.1% |
| ATR x3 | 85 | -5.2% | -39.4% |
| ATR x4 | 78 | +6.8% | -28.8% |
| ATR x6 | 57 | +10.8% | -21.4% |
| ATR x8 | 41 | +13.5% | -22.4% |

Monotonically improves with a looser multiplier but flattens out well
below baseline even at 8x (where trade count has nearly converged to
baseline's) — going looser still would only approach parity, not beat
it. **A fifth real data point for the established VR pattern**: this
stream's return is concentrated in a handful of outlier trades that need
maximum room; any mechanism that tightens or varies its trail (ratcheting
trail and hard leading_sell from idea #7, now ATR-adaptive too) hurts it.
Plumbing (`position_monitor.py`/`executor.py`/`live_replay_stream.py`/
`market_data.py` changes) is inert in production — no live stream sets
`trailing_stop_atr_multiplier` — but sitting uncommitted pending a
decision on whether to test it against MR/BS/DH (untested, and per idea
#7's own finding, VR is specifically the outlier-concentrated stream —
the other three may respond completely differently) before committing.

---

## 13. Volatility-harvesting constant-mix rebalancing bands

**Status:** raw

Pitched 2026-09-14, in response to wanting genuinely different structural
bets — not another "one signal, one static exit" stream. Hold a target
BTC/cash split (e.g. 50/50) and mechanically rebalance back to that
target whenever price drifts past a threshold band — i.e. systematically
sell-high/buy-low as price oscillates, with no directional entry signal
at all. This is a well-known technique in portfolio theory ("volatility
pumping" / Shannon's Demon), and it matches this project's own mission
statement — "BTC is the vehicle, cash growth is the product... converts
market volatility into realized cash returns" (CLAUDE.md) — more
literally than any stream actually built so far.

Structurally distinct from everything live: no `core_signal`, no
`trailing_stop_pct`. Would need its own new `slot_mode`, separate from
single/staggered/scale_up/scale_down/cascade — this isn't a parameter on
the existing engine, it's a different position-management primitive.

**Real risk:** performs best in genuinely range-bound/choppy conditions
(which happens to be a regime gap none of the 4 live streams cover well
per [[project_stream_design_philosophy]]) but bleeds slowly in a strong
sustained trend — constantly trimming into a rally, buying into a
persistent decline. Same fundamental tradeoff as any mean-reversion
strategy; needs an honest backtest across bull/bear/chop windows before
getting attached to the "matches the mission statement" framing alone.

**Next step:** backtest a simple version first (e.g. 50/50 target,
rebalance at ±10% drift) against Primary v2 and the Gauntlet's bear
windows, compared against buy-and-hold and against the existing streams'
performance specifically during their own worst (choppiest/lowest-vol)
windows.

---

## 14. Grid / range trading (non-directional resting-order ladder)

**Status:** raw — blocked on idea #4 (regime classifier) before it's
safe to even backtest.

Pitched 2026-09-14 alongside #13 — same non-directional family (no
entry-signal-then-exit shape) but mechanically different: rest a ladder
of limit buy orders below current price and limit sell orders above,
profiting from price oscillating through the grid regardless of
direction, instead of #13's continuous threshold rebalancing.

**Why this is gated, not just raw:** a grid has no protection if price
breaks the assumed range and keeps running one direction — orders on the
losing side just keep filling while the other side sits dead, which
looks exactly like an unbounded, ungraduated cascade the moment the range
assumption breaks. That's the same root failure mode that killed
cascade/DCA (idea #2) — a fixed-shape structure making an implicit bet
that price stays bounded, with no real backstop when it doesn't.

**Next step:** don't build or backtest this until idea #4 (regime
classifier) exists in at least a simple form, as a genuine "is this
actually a range" gate — building this on a promising-looking backtest
alone would risk reproducing idea #2's exact mistake.

---

## 15. Cross-asset lead-lag as a signal input only (never traded)

**Status:** raw — gated behind a new data source decision

Pitched 2026-09-14. Different from idea #9 (trading ETH/SOL/DOGE
directly as their own Models, not pursued): this keeps every actual
trade in BTC/USD only, respecting the BTC-only constraint (CLAUDE.md),
and only asks whether another asset's price action *leads* BTC's by some
measurable lag — useful purely as an additional signal/filter on
existing or new BTC streams, never as a traded instrument itself.

**Checked 2026-09-14:** `market_data` currently holds BTC/USD only (no
`symbol` column, single implicit series) — there is no other asset price
history to test a lead-lag relationship against yet. This is a smaller
lift than idea #9 (read-only signal data, never executed against), but
still requires deciding to ingest a second asset's history, which is a
new external data pull and needs explicit go-ahead first per the
standing external-source consent rule, same as any new API/data
provider.

**Next step:** none until a decision is made to pull in a second asset's
OHLCV history specifically for this purpose — don't backtest a lead-lag
relationship on data that doesn't exist yet.

---

---

## 16. ML feature-importance research tool for stream design (not a live gate)

**Status:** tested — dead end (2026-09-17). Built and genuinely run three
separate ways; all three converge on the same negative result. Not
closing this because the tool didn't work — closing it because it worked
correctly and gave a consistent, real answer.

Pitched 2026-09-17, after confirming (grep across `src/`, `docs/`, `requirements.txt`
— zero real hits, one false positive on "shape" substring-matching "shap")
that this project has never used any real ML/tree-based method anywhere.
Every stream so far has been designed by hand: pick a signal, pick 2-4
filters by intuition, test, manually sweep a few parameter values. This is
a genuinely different and untried method — use a tree-based model to find
which indicator combinations actually separate winning entries from
losing ones, systematically, across the whole feature space at once,
instead of guessing interactions by hand.

**Explicitly scoped as a research tool, not a live gate** — this is a
narrower, safer scope than idea #4 (regime classifier), which proposes a
frozen model actually gating live entries. This tool's job is to surface
candidate combinations for a human to then design a real stream around
through the normal pipeline — its output is never trusted directly.

**Concrete plan:**
- **Features:** every indicator already computed in `indicators.py` (RSI,
  ATR, ADX, Bollinger bandwidth, volume ratio, MACD, EMA spread) plus
  candidates not yet wired into any live stream — F&G value/rate-of-change,
  time-of-day/day-of-week (idea #5), bars-since-new-high (idea #11),
  ATR percentile-of-its-own-history (regime context, distinct from ADX).
- **Model:** gradient-boosted trees (not deep learning) — handles
  nonlinear feature interactions well, and `feature_importances_`/SHAP
  values are directly human-readable, not a black box. Needs a new local
  Python dependency (scikit-learn or xgboost/lightgbm) — a library, not an
  external service, so this doesn't touch the standing external-source
  consent rule.
- **The real open design question — how to label a candle as
  "good" or "bad":**
  - *Option A, forward-return labeling:* is `close[t+N]` up more than some
    threshold from `close[t]`. Simple, asset-agnostic, but ignores real
    exit mechanics (fees, trailing stops) that determine actual P&L — a
    candle can show a good forward return that a real stream would never
    have captured (stopped out early, or filters never fired).
  - *Option B, simulated-trade labeling:* apply one standardized entry +
    exit (e.g. a generic fixed trailing stop, real fees) at every
    candidate candle and label by the real simulated trade outcome.
    Heavier to compute, but isolates "is this entry condition good" from
    "which exit mechanic is good" by holding the exit fixed across every
    sample — closer to what actually matters for stream design.
  - Leaning toward B for honesty about real economics, but this is the
    actual decision worth making deliberately before writing code, not
    silently defaulting.

**Guardrail, non-negotiable:** BTC's price history is one long
non-stationary time series, not many independent samples — a tree given
enough features will find combinations that look great in-sample and are
pure noise out-of-sample. This is the exact overfitting mechanism behind
the "over 90% of academic strategies fail live" finding from 2026-09-17's
research pass. **This tool only ever produces hypotheses.** Every
combination it surfaces still goes through the full existing pipeline —
build as a real stream, Stream Tester, live-replay backtest, Gauntlet,
adversarial-code-review gate ([[feedback_model_finalization_adversarial_gate]])
— never adopted because a model liked it in training. Needs its own
train/test split respecting time order (train on earlier years, evaluate
on strictly later ones) at minimum, not a random shuffle split, which
would leak future information into training the same way lookahead bias
does anywhere else in this codebase.

**Next step:** decide Option A vs. B for labeling (leaning B), then build
a first pass against a single well-understood window (Primary v2) purely
to see if feature importances surface anything already-known (e.g., does
it independently rediscover that volume+RSI-range matters for Volume
Raider) as a sanity check before trusting it on anything genuinely new.

**Built (2026-09-17):** `src/research/entry_quality_study.py`. Option B
labeling (simulated trailing-stop trade, real fees) confirmed as the
right call. Ran three genuinely different ways:

1. **First pass** — every hourly candle in Primary v2 as a sample, one
   80/20 time-ordered split. Train R² 0.71, **test R² -0.29**; train
   sign-accuracy 82.9%, test 53.3% (worse than the ~67% naive
   majority-class baseline). `bars_since_new_high` dominated importance
   (71%) but turned out to be mechanically entangled with the label
   itself (a candle far past its own peak is closer to where an 8% trail
   off that peak already triggers) — not a discovered edge.
2. **Yearly walk-forward, same features, daily-subsampled** (2018-2026,
   8 folds, fixing the overlapping-sample problem from pass 1) — **test
   R² negative in 7/8 years**, test sign-accuracy at or below each year's
   own majority-class baseline in every single fold, regardless of
   regime (2019 recovery, 2020 COVID, 2021 bull, 2022 bear, 2023 chop,
   2024 halving, 2025-2026). `bars_since_new_high` still dominated
   (61% mean importance, std 0.08 — suspiciously stable across every
   regime) while still producing zero real generalization.
3. **Corrected framing per direct feedback** (user: "I assumed you were
   using all variables... looking for the tree to find the best
   combination whether core_signals, filters, or whatever else") — core-
   signal identity added as real features (`signal_volume_surge`,
   `signal_ema_crossover`, `signal_rsi_recovery`, `signal_range_breakout`,
   using the actual `generate_signals()` from `signals.py`, not
   reinvented), sample restricted to the 6,310 real historical candles
   where at least one genuinely fired. **Same result** — test R² negative
   in 7/8 years, worse than baseline every year. Core-signal identity
   itself came in at ~0 importance (0.000-0.004) in every fold — as
   tested, which pattern triggers the entry barely mattered next to
   Fear & Greed level/rate-of-change and ATR-based volatility (~68%
   combined importance), which were themselves suspiciously stable
   across every regime the same way `bars_since_new_high` was in pass 2.

**Root cause / honest read:** individual-trade win/loss from a snapshot
of technical + sentiment features, at hourly granularity, does not
appear to be predictable by this method — regardless of label framing
(every candle vs. signal-conditioned) or feature set (generic indicators
vs. core-signal identity included). The one consistent pattern across
all three attempts: whichever feature dominates importance is rock-
stable across every regime AND produces zero real generalization at the
same time — a strong tell that the "important" feature is capturing
which multi-year era it is (sentiment/volatility level drifts over
BTC's cycles) rather than anything about the specific moment being a
good entry. This is consistent with, not contradicted by, this
project's own real results: Model 1/2's actual edge (13.6%/15.9% ann)
comes from aggregate expectancy across many hand-designed trades with
tuned entry+exit+filter combinations validated end-to-end through
live-replay, not from something separable trade-by-trade out of a
feature snapshot. Also independently consistent with the 2026-09-17
research pass's own citation that over 90% of academic strategies with
strong in-sample backtest performance fail once real capital is behind
them.

**Verdict:** the tool itself worked as designed — it's precisely the
kind of consistent, convergent null result a well-built adversarial
research process is supposed to produce, not a sign it was built wrong.
Don't revisit without a genuinely different angle (a materially
different label horizon/separation, or accepting that per-trade
outcome may just not be predictable this way and refocusing on
aggregate-expectancy questions instead, which is what stream design
already does by hand).

---

*Also raised 2026-09-14, deliberately not given full entries yet:*
**on-chain/exchange-flow data** (whale wallet movements, exchange
netflows) — a genuinely different signal class, but a new external data
provider, same consent gate as #15. **True two-sided market-making**
(resting both a buy and sell around the spread to capture it directly) —
a real out-of-the-box mechanic, but likely thin margin against the 0.40%
maker fee at this trade size, and needs meaningfully more execution
engine complexity (continuous quote/inventory management) than anything
built so far. Revisit either if they come up again with more specific
motivation.
