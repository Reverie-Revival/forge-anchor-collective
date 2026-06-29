# Database Schema

**Database:** `forge_anchor` (PostgreSQL, local during build, server when live)
**Schemas:** `backtest`, `live`, `reporting` + shared `market_data` table

> **Status:** Early development — schema is subject to change. Live schema is designed but not yet built.

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

## backtest schema

Two types of testing live here:
- **Stream tests** — individual streams tested in isolation during design/tuning
- **Model tests** — all streams running together as a full model, used to gate deployment

Freely rebuilt during development. Never contains real money.

---

### backtest.stream_tests

Captures every stream tuning run you choose to save. One row per saved test.
Stores the full parameter config and all key performance metrics for comparison.

`run_number` groups tests with identical parameters (same config, different date windows). `window_name` labels the date range: Primary / Full History / Recent / custom.

```sql
  test_id               SERIAL PRIMARY KEY
  stream_name           VARCHAR(100) NOT NULL     -- e.g. "Momentum Rider"
  stream_version        VARCHAR(20) NOT NULL      -- "v1", "v2"
  run_number            INTEGER                   -- groups same-config tests (1, 2, 3...)
  window_name           VARCHAR(50)               -- "Primary", "Full History", "Recent"
  parameters            JSONB NOT NULL            -- full config snapshot at test time
  test_start            TIMESTAMPTZ               -- backtest date range start
  test_end              TIMESTAMPTZ               -- backtest date range end
  n_slots               SMALLINT NOT NULL         -- 1 or 2
  initial_capital       NUMERIC(10,2) NOT NULL    -- starting $ (n_slots × $10)
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
  slot_count      SMALLINT DEFAULT 2      -- max concurrent positions for this stream
  lot_size_usd    NUMERIC(10,2) DEFAULT 10.00  -- $ per position; stream capital = slot_count × lot_size_usd
  locked_test_id  INTEGER REFERENCES backtest.stream_tests  -- winning run
  grade           SMALLINT               -- 1–5
  locked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
  description     TEXT                   -- plain-English explanation shown in Stream Tester sidebar
  notes           TEXT                   -- key decisions / iteration summary
```

**Capital allocation:** Each stream owns its slice of model capital independently.
Total model capital = Σ(lot_size_usd × slot_count) across all streams.
A model can have 3–5 streams — not necessarily 5. High-conviction streams can get more capital or more slots.
**Minimum lot_size_usd: $10.** Below this, Kraken's minimum order size and the 0.50% round-trip fee make positions impractical. Enforced via CHECK constraint.
Allocation is decided at model assembly, not during stream tuning — locked streams get placeholder defaults until model-level testing finalizes weights.

### backtest.model_tests

A full model backtest run — all streams running together as a unit.
Used to validate the complete model before live deployment.

```sql
  model_test_id            SERIAL PRIMARY KEY
  model_id                 INTEGER NOT NULL REFERENCES backtest.models
  run_type                 VARCHAR(20) NOT NULL   -- 'historical' | 'paper'
  simulation_start         TIMESTAMPTZ NOT NULL
  simulation_end           TIMESTAMPTZ            -- null if paper test still running
  went_live_at             TIMESTAMPTZ            -- paper only: when real-time feed began
  status                   VARCHAR(20) NOT NULL   -- 'running' | 'completed'
  selected_for_deployment  BOOLEAN DEFAULT FALSE  -- marks the paper test that earned live deployment
  configuration            JSONB NOT NULL         -- full model config snapshot at run start
  notes                    TEXT
  created_at               TIMESTAMPTZ DEFAULT NOW()
```

### backtest.lots

Every unit of capital moving through a slot in a model test. The full trade-level record.

```sql
  lot_id           BIGSERIAL PRIMARY KEY
  model_test_id    INTEGER NOT NULL REFERENCES backtest.model_tests
  model_id         INTEGER NOT NULL REFERENCES backtest.models
  stream_id        INTEGER NOT NULL REFERENCES backtest.streams
  slot_number      SMALLINT NOT NULL              -- 1 or 2
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

Capital compounds within the slot.

---

## live schema

Real money. Real Kraken orders. Precious — never touched carelessly.
**Not yet built** — will mirror backtest structure with Kraken order IDs added.
`live.models.based_on_model_test_id` will link every live deployment to the paper test that earned it.

---

## reporting schema

Views that union backtest and live data for cross-environment comparison.
Analytics only queries reporting — never backtest or live directly.

### reporting.all_lots (view)
Unions `backtest.lots` and `live.lots` with a source tag.

### reporting.model_performance (view)
Aggregated metrics per model test: total return, annualized return, max drawdown, win rate, grade.

### reporting.stream_performance (view)
Aggregated metrics per stream across all runs — which stream lineages consistently perform best?

---

## Key Design Decisions

- **Two test levels** — `stream_tests` for individual stream tuning (summary only), `model_tests` + `lots` for full model validation (per-trade detail)
- **Capital compounds within a slot** — opening_capital of each new lot = closing_capital of the previous one
- **Parameters are snapshotted** — both `stream_tests.parameters` and `model_tests.configuration` store a full JSONB copy of the config at test time. Historical runs are never affected by later parameter changes
- **`selected_for_deployment`** — marks which paper test earned live deployment, creating a full audit chain from experiment to production
