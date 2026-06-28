# Model Validation Workflow

How a model moves from idea to live deployment. Every model goes through the same cycle.

---

## The Full Cycle

```
1. DESIGN        — choose 5 streams, set initial parameters
2. EXPERIMENT    — run many historical backtests, tune aggressively
3. PAPER TEST    — run multiple paper tests in parallel, prove against live data
4. SELECT        — pick the best paper test configuration
5. DEPLOY        — $100 live on Kraken
6. REPEAT        — while current model runs live, begin next model's design phase
```

---

## Phase 1 — Experimental Historical Backtests

**Purpose:** Find the best stream combinations and parameter settings for the model.

- Run as many times as needed against stored historical data
- Fast — months of simulation in seconds
- Try different stream configurations, trailing stop percentages, entry thresholds
- Compare results across runs to find what performs best across diverse market conditions
  (bull runs, bear markets, sideways chop — the model must hold up across all of them)
- No limit on number of runs — this is pure exploration
- All runs logged in `backtest.model_tests` with `run_type = 'historical'`

**Output:** A set of promising stream configurations worth promoting to paper testing.

---

## Phase 2 — Paper Tests (Multiple, Concurrent)

**Purpose:** Prove the top configurations against live market data before committing real money.

- Each promising configuration from Phase 1 gets its own paper test
- Multiple paper tests run simultaneously for the same model — no limit
- No real orders placed — Kraken live feed is used for prices only
- All logged in `backtest.model_tests` with `run_type = 'paper'`

### Configurable Simulation Start Date
Every paper test has a `simulation_start` date — set independently of when you actually kick it off.

**Why this matters:** If Paper Test 1 starts in January and Paper Test 2 starts in March, both can be set to the same `simulation_start` date so their results are directly comparable. The system fast-replays historical data from `simulation_start` to today, then transitions to real-time.

```
[simulation_start] → → → → [went_live_at] → → → → [now, running]
     ↑                            ↑
historical replay (fast)    real-time begins (real clock speed)
```

### Paper Tests Run in Perpetuity
Paper tests are never cancelled when a better configuration is found. All run until a deployment decision is made. `selected_for_deployment = TRUE` marks the winner — all others continue as reference data.

**Setting a new paper test off:**
1. Experimental backtests identify a promising new configuration
2. New paper test started with the same `simulation_start` as existing tests
3. It fast-replays history, catches up, then runs forward alongside existing tests
4. Now you have N+1 paper tests running in parallel

---

## Phase 3 — Deploy Decision

**Gate:** Backtest confidence + paper test performance. No calendar requirement.

**Questions to answer before deploying:**
- Does the selected paper test beat Model N-1's live results over the same period?
- Does it hold up across the paper test's forward (real-time) period?
- Do experimental backtests across diverse market conditions support the configuration?

When ready:
1. Mark the winning paper test as `selected_for_deployment = TRUE`
2. Deploy $100 live on Kraken as Model N
3. `live.models.based_on_model_test_id` links back to the winning paper test

---

## Phase 4 — Overlap (Parallel Development)

While Model N runs live, Model N+1 development begins immediately.

- Model N runs its full life per the model commitment rule — no early shutdown
- Model N+1 goes through experimental backtests and paper tests concurrently
- The paper tests for Model N+1 use Model N's live deployment date as `simulation_start`
  so they can be compared directly against Model N's actual live performance
- Every 2-3 months, assess whether a new model is ready to deploy

---

## Comparison Framework

At any point you can compare across environments and model versions:

| Comparison | What it answers |
|---|---|
| Paper test vs historical backtest (same config) | Did real-time match what backtests predicted? |
| Paper test vs live Model N (same time period) | Would this config have beaten the current live model? |
| Paper Test A vs Paper Test B | Which configuration performs better live? |
| Live Model N vs Live Model N+1 | Which deployed model is actually winning? |
| Stream X across all models | Which stream lineages consistently outperform? |

All of these comparisons are available through the `reporting` schema views.

---

## Model Grading

Applied to live models based on realized performance over time:

| Grade | Label | Criteria |
|---|---|---|
| 5 | Elite | 20%+ annualized, sustained 2+ years |
| 4 | Strong | Consistently beats S&P 500 (10-19%) |
| 3 | Passing | Roughly matches S&P (8-12%) |
| 2 | Weak | Positive but below S&P |
| 1 | Poor | Break-even or loss |

Grade 5 sustained for 2+ years → candidate for increased capital allocation.
