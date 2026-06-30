# Database Schema

**Database:** `forge_anchor` (PostgreSQL, local during build, server when live)
**Schemas:** `backtest`, `live`, `reporting` + shared `market_data` / `timeframe_presets` tables

> **Status:** v2 architecture. `live` schema is designed but not yet built.

---

## Shared tables

### market_data

Stores all raw BTC/USD price history. Lives outside both schemas — feeds everything.
Single interval: 15-minute candles from January 1, 2017 to present (~332,000 rows).
See ADR 006 for the full reasoning behind interval and history decisions.

```sql
market_data
  candle_id     BIGSERIAL PRIMARY KEY
  timestamp     TIMESTAMPTZ NOT NULL UNIQUE
  open          NUMERIC(12,2) NOT NULL
  high          NUMERIC(12,2) NOT NULL
  low           NUMERIC(12,2) NOT NULL
  close         NUMERIC(12,2) NOT NULL
  volume        NUMERIC(20,8) NOT NULL
```

Index on `timestamp` — nearly every query filters by time range.

---

### sentiment_data

Daily Fear & Greed Index values. Backfilled Feb 2018 → present via `python -m src.data.sentiment`. Feeds the sentiment gate attribute on any stream.

```sql
sentiment_data
  date        DATE PRIMARY KEY
  fng_value   SMALLINT NOT NULL     -- 0 (Extreme Fear) → 100 (Extreme Greed)
  fng_label   VARCHAR(30) NOT NULL  -- "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
```

Candles before Feb 2018 have no corresponding row — the backtester skips the sentiment gate for those candles rather than blocking them.

---

### timeframe_presets

Standard date windows used across stream tests and model tests. Replaces free-text `window_name` — tests reference a preset by FK so the window definition is consistent and queryable.

```sql
timeframe_presets
  preset_id    SERIAL PRIMARY KEY
  name         VARCHAR(50) NOT NULL UNIQUE   -- "Primary Window", "Full History", etc.
  start_date   DATE NOT NULL
  end_date     DATE                          -- NULL = open-ended (runs to present)
  description  TEXT
  is_active    BOOLEAN NOT NULL DEFAULT TRUE
  created_at   TIMESTAMPTZ DEFAULT NOW()
```

**Standard presets (seeded):**

| Name | Start | End | Purpose |
|---|---|---|---|
| Primary Window | 2019-01-01 | 2023-12-31 | Main gate: varied regimes (bull/bear/recovery) |
| Full History | 2018-01-01 | open | All available data including sentiment coverage |
| Recent | 2024-01-01 | open | Post-halving behavior, 2025 ATH cycle |
| 2026 YTD | 2026-01-01 | open | Current year performance |

Custom date ranges are always available for one-off tests and are stored directly as `custom_start`/`custom_end` timestamps on the test row.

---

## backtest schema

Two types of testing live here:
- **Stream tests** — individual streams tested in isolation during design/tuning
- **Model tests** — all streams running together as a full model, used to gate deployment

Freely rebuilt during development. Never contains real money.

---

### backtest.stream_tests

Captures every stream tuning run you choose to save. One row per saved test.
Stores the full parameter config and all key performance metrics for comparison.

`run_number` groups tests with identical parameters (same config, different date windows). Date range is either a `preset_id` FK (standard window) or explicit `custom_start`/`custom_end` — exactly one must be set (CHECK constraint enforced).

```sql
  test_id               SERIAL PRIMARY KEY
  stream_name           VARCHAR(100) NOT NULL     -- e.g. "Momentum Rider"
  stream_version        VARCHAR(20) NOT NULL      -- "v1", "v2"
  run_number            INTEGER                   -- groups same-config tests (1, 2, 3...)
  preset_id             INTEGER REFERENCES timeframe_presets  -- standard window FK
  custom_start          TIMESTAMPTZ               -- set only when not using a preset
  custom_end            TIMESTAMPTZ               -- null = open-ended custom range
  simulation_start      TIMESTAMPTZ               -- actual first candle after warmup clip
  simulation_end        TIMESTAMPTZ               -- actual last candle
  slot_count            SMALLINT NOT NULL DEFAULT 1  -- max concurrent positions
  slot_mode             VARCHAR(30) NOT NULL DEFAULT 'single'  -- 'single' | 'scale_down' | 'scale_up'
  parameters            JSONB NOT NULL            -- full config snapshot at test time
  initial_capital       NUMERIC(10,2) NOT NULL    -- starting $ (lot_size_usd × slot_count)
  ending_balance        NUMERIC(10,4)             -- final $ after all trades
  total_trades          INTEGER
  win_rate              NUMERIC(5,4)              -- 0.0 to 1.0
  total_pnl             NUMERIC(10,4)
  total_return_pct      NUMERIC(8,2)
  annualized_return_pct NUMERIC(8,2)
  avg_winner_pct        NUMERIC(8,2)
  avg_loser_pct         NUMERIC(8,2)
  profit_factor         NUMERIC(8,2)
  max_drawdown_pct      NUMERIC(8,2)
  avg_hold_candles      NUMERIC(8,1)
  notes                 TEXT
  saved_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

No per-trade detail here — just summary metrics. Designed for fast iteration and comparison.
A full pkl payload is stored at `src/app/runs/{test_id}.pkl` for chart rendering in the stream tester.

**`timeframe_label`** is computed at query time via `COALESCE(tp.name, formatted_custom_dates)` — not stored.

---

### backtest.models

One row per model version defined for backtesting.

```sql
  model_id        SERIAL PRIMARY KEY
  model_version   INTEGER NOT NULL        -- 1, 2, 3...
  description     TEXT
  created_at      TIMESTAMPTZ DEFAULT NOW()
```

### backtest.streams

Streams locked into a model after validation. One row per stream per model.
`locked_test_id` points to the `stream_tests` row (Primary window) that earned the lock.

```sql
  stream_id       SERIAL PRIMARY KEY
  model_id        INTEGER NOT NULL REFERENCES backtest.models
  stream_name     VARCHAR(100) NOT NULL   -- e.g. "Momentum Rider"
  stream_version  VARCHAR(10) NOT NULL    -- "v1", "v2"
  strategy_type   VARCHAR(50) NOT NULL
  parameters      JSONB NOT NULL          -- all tunable thresholds
  slot_count      SMALLINT NOT NULL DEFAULT 1   -- max concurrent positions
  slot_mode       VARCHAR(30) NOT NULL DEFAULT 'single'  -- 'single' | 'scale_down' | 'scale_up'
  lot_size_usd    NUMERIC(10,2) DEFAULT 10.00   -- $ per position; CHECK >= 10
  locked_test_id  INTEGER REFERENCES backtest.stream_tests  -- winning Primary window run
  grade           SMALLINT               -- 1–5
  locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
  description     TEXT                   -- plain-English explanation shown in Stream Tester sidebar
  notes           TEXT                   -- key decisions / iteration summary
```

**Capital allocation:** Each stream owns its slice of model capital independently.
Total model capital = Σ(lot_size_usd × slot_count) across all streams.
A model can have 3–5 streams — not necessarily 5. High-conviction streams can get more capital or more slots.
**Minimum lot_size_usd: $10.** Enforced via CHECK constraint.
Allocation is decided at model assembly, not during stream tuning — locked streams get placeholder defaults until model-level testing finalizes weights.

**Slot modes:**
- `single` — only slot 1 runs; slot_count ignored for dispatch purposes
- `scale_down` — slot 2 enters when slot 1 is open and price drops `slot2_trigger_pct` below entry (DH pattern: average down)
- `scale_up` — slot 2 enters when slot 1 is open, price is up `slot2_trigger_pct`, and the original signal fires again (MR pattern: pyramid up)

### backtest.model_tests

A full model backtest run — all streams running together as a unit.
Used to validate the complete model before live deployment.

`run_number` groups runs with the same allocation config (same streams + weights, different date windows). Date range follows the same preset/custom FK pattern as stream_tests.

```sql
  model_test_id            SERIAL PRIMARY KEY
  model_id                 INTEGER NOT NULL REFERENCES backtest.models
  run_type                 VARCHAR(20) NOT NULL   -- 'historical' | 'paper'
  run_number               INTEGER                -- groups same-allocation runs
  preset_id                INTEGER REFERENCES timeframe_presets
  custom_start             TIMESTAMPTZ
  custom_end               TIMESTAMPTZ
  simulation_start         TIMESTAMPTZ NOT NULL
  simulation_end           TIMESTAMPTZ            -- null if paper test still running
  status                   VARCHAR(20) NOT NULL   -- 'running' | 'completed'
  configuration            JSONB NOT NULL         -- {allocations: {stream: {lot_size_usd, slot_count, slot_mode}}}
  total_capital            NUMERIC(10,2)          -- Σ(lot_size_usd × slot_count) across all streams
  ending_balance           NUMERIC(10,4)
  total_trades             INTEGER
  win_rate                 NUMERIC(5,4)
  total_pnl                NUMERIC(10,4)
  total_return_pct         NUMERIC(8,2)
  annualized_return_pct    NUMERIC(8,2)
  max_drawdown_pct         NUMERIC(8,2)
  notes                    TEXT
  created_at               TIMESTAMPTZ DEFAULT NOW()
```

A full pkl payload is stored at `src/app/model_runs/{model_test_id}.pkl` for chart rendering in the Model Tester.

### backtest.lots

Every unit of capital moving through a slot in a model test. The full trade-level record.

```sql
  lot_id           BIGSERIAL PRIMARY KEY
  model_test_id    INTEGER NOT NULL REFERENCES backtest.model_tests
  model_id         INTEGER NOT NULL REFERENCES backtest.models
  stream_id        INTEGER NOT NULL REFERENCES backtest.streams
  slot_number      SMALLINT NOT NULL              -- 1, 2, ...
  lot_sequence     INTEGER NOT NULL               -- 1st lot this slot ever had, 2nd, etc.
  status           VARCHAR(10) NOT NULL           -- 'CASH' | 'OPEN' | 'CLOSED'
  opening_capital  NUMERIC(12,2) NOT NULL         -- $ at start of this lot
  btc_quantity     NUMERIC(20,8)                  -- null if CASH
  entry_price      NUMERIC(12,2)                  -- null if CASH
  high_water_mark  NUMERIC(12,2)                  -- trailing stop ceiling, updated while OPEN
  exit_price       NUMERIC(12,2)                  -- null if CASH or OPEN
  closing_capital  NUMERIC(12,2)                  -- null until CLOSED
  realized_pnl     NUMERIC(12,2)
  entry_reason     TEXT
  exit_reason      TEXT
  opened_at        TIMESTAMPTZ
  closed_at        TIMESTAMPTZ
```

**Lot state machine:**
```
CASH → OPEN → CLOSED → (new CASH lot, opening_capital = previous closing_capital)
```

Each slot has an independent `high_water_mark` — trailing stops fire independently per lot.
Capital compounds within the slot.

---

## live schema

Real money. Real Kraken orders. Precious — never touched carelessly.
**Not yet built** — will mirror backtest structure with Kraken order IDs added.

**Safety rule:** `reset_backtest.sql` explicitly excludes all `live.*` tables. Any live schema change requires a dedicated, explicitly reviewed migration — never casual DDL.

---

## reporting schema

Views that union backtest and live data for cross-environment comparison.
Analytics only queries reporting — never backtest or live directly.

### reporting.all_lots (view)
Unions `backtest.lots` and `live.lots` with a source tag. This is the only view currently built.

---

## Key Design Decisions

- **`timeframe_presets` FK over free-text window names** — consistent window definitions across all test runs; `timeframe_label` is computed at query time via COALESCE
- **`slot_mode` per stream** — slot 2 entry behavior is intentional and stream-specific, not accidental
- **Two test levels** — `stream_tests` for individual stream tuning (summary only), `model_tests` + `lots` for full model validation (per-trade detail)
- **Capital compounds within a slot** — opening_capital of each new lot = closing_capital of the previous one
- **Parameters are snapshotted** — both `stream_tests.parameters` and `model_tests.configuration` store a full JSONB copy of the config at test time. Historical runs are never affected by later parameter changes
