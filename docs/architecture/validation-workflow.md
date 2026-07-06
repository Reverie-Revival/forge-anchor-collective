# Model Validation Workflow

How a stream moves from idea to locked, and how a model moves from locked streams to live deployment.

---

## The Full Cycle

```
1. STREAM DESIGN   — design stream signal, parameters, regime complement
2. STREAM TUNING   — backtest across presets, iterate, lock a stream_config version
3. MODEL ASSEMBLY  — combine locked stream configs, set allocations, run model-level backtest
4. DEPLOY          — $100 live on Kraken; backtest confidence is the gate
5. REPEAT          — while current model runs live, begin next model's stream design phase
```

No mandatory paper trading phase. $100 live capital is low enough that live deployment IS the real-world test. See ADR 005 for the reasoning.

---

## Phase 1 — Stream Design and Tuning

**Purpose:** Find the best signal configuration for each stream in isolation.

- Each stream is tuned one at a time — finish one before starting the next
- Run presets via the Stream Tester app (Stream Tester → select stream → select config version → Run All Presets)
- Results auto-save to `backtest.stream_tests` on `(stream_config_id, preset_id)` — re-running replaces, no duplicates
- Test across all standard presets: Primary v2, Full History, Recent, 2026 YTD
- Iterate parameters by creating new config versions in `backtest.stream_configs`
- When a config version's signal is solid across all presets, lock it: the `stream_config_id` becomes the reference for model assembly

**Key constraint:** Tune the signal, not the allocation. `lot_size_usd` is ignored during stream tuning — it gets set at model assembly.

**Output:** A locked `stream_config_id` for each stream, with validated results across all presets.

---

## Phase 2 — Model Assembly

**Purpose:** Prove the full configuration — all streams running simultaneously with capital allocation set.

1. Create a model record in `backtest.models`
2. Populate `backtest.model_streams` with the locked stream configs and final `lot_size_usd` per stream
3. Run model-level backtest via the Model Tester app — all streams fire against the same historical data concurrently
4. Evaluate results across all presets: Primary v2 is the primary gate, others confirm robustness

**Deployment gate:**
- Primary v2 annualized return beats S&P 500 (~10%) across diverse market conditions
- No single window is deeply negative
- Max drawdown is acceptable (historically ≤ 20% for Model 1)

**Output:** A `model_test_id` with validated results — this is the record that authorized the deployment.

---

## Phase 3 — Deploy Decision

**Gate:** Backtest confidence. No calendar requirement. No paper testing required.

When ready:
1. Set `backtest.models.status = 'deployed'`
2. Deploy $100 live on Kraken — insert rows into `live.models`, `live.streams`
3. Executor begins running on cron schedule (GitHub Actions + Supabase)

---

## Phase 4 — Overlap (Parallel Development)

While Model N runs live, Model N+1 development begins immediately.

- Model N runs its full life per the model commitment rule — no early shutdown
- Model N+1 goes through stream design and model assembly concurrently
- Every 2-3 months, assess whether a new model is ready to deploy
- If Model N+1 backtest matches or beats Model N, deploy it with its own $100

---

## Comparison Framework

At any point you can compare across environments and model versions:

| Comparison | What it answers |
|---|---|
| Stream v2 vs v1 results | Did the parameter iteration actually help? |
| Model N+1 backtest vs Model N backtest (same period) | Would the new model have beaten the current? |
| Live Model N vs backtest prediction | Did real-world match what backtests predicted? |
| Stream X across all models | Which stream lineages consistently outperform? |

All live comparisons are available through the `reporting` schema views.

---

## Model Grading

Applied to live models based on realized annualized return:

| Grade | Label | Criteria |
|---|---|---|
| 5 | Elite | 20%+ annualized, sustained 2+ years |
| 4 | Strong | Consistently beats S&P 500 (10-19%) |
| 3 | Passing | Roughly matches S&P (8-12%) |
| 2 | Weak | Positive but below S&P |
| 1 | Poor | Break-even or loss |

Grade 5 sustained for 2+ years → candidate for increased capital allocation.
