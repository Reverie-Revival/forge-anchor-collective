# Database Schema

**Database:** `forge_anchor` (PostgreSQL, local during build, server when live)
**Schemas:** `backtest`, `live`, `reporting` + shared `market_data` table

---

## Shared — market_data

Stores all raw BTC price history. Lives outside both schemas — feeds everything.

```sql
market_data
  candle_id     BIGSERIAL PRIMARY KEY
  timestamp     TIMESTAMPTZ NOT NULL
  interval      VARCHAR(10) NOT NULL   -- '1h', '4h', '1d'
  open          NUMERIC(12,2) NOT NULL
  high          NUMERIC(12,2) NOT NULL
  low           NUMERIC(12,2) NOT NULL
  close         NUMERIC(12,2) NOT NULL
  volume        NUMERIC(20,8) NOT NULL
  UNIQUE (timestamp, interval)
```

Index on `(timestamp, interval)` — nearly every query filters by time range.

---

## backtest schema

Covers both historical simulation runs and paper tests (real-time, no real money).
Freely rebuilt during development. Never contains real money.

### backtest.models
```sql
  model_id        SERIAL PRIMARY KEY
  model_version   INTEGER NOT NULL        -- 1, 2, 3...
  description     TEXT
  created_at      TIMESTAMPTZ DEFAULT NOW()
```

### backtest.streams
```sql
  stream_id       SERIAL PRIMARY KEY
  model_id        INTEGER NOT NULL REFERENCES backtest.models
  stream_name     VARCHAR(100) NOT NULL   -- e.g. "Momentum Rider"
  stream_version  VARCHAR(10) NOT NULL    -- "v1", "v2"
  strategy_type   VARCHAR(50) NOT NULL
  parameters      JSONB NOT NULL          -- all tunable thresholds
  slot_count      SMALLINT DEFAULT 2
```

### backtest.runs
The core table that distinguishes historical runs from paper tests.

```sql
  run_id                   SERIAL PRIMARY KEY
  model_id                 INTEGER NOT NULL REFERENCES backtest.models
  run_type                 VARCHAR(20) NOT NULL   -- 'historical' | 'paper'
  simulation_start         TIMESTAMPTZ NOT NULL   -- configurable, set for fair comparison
  simulation_end           TIMESTAMPTZ            -- null if paper (still running)
  went_live_at             TIMESTAMPTZ            -- paper only: when replay ended, real-time began
  status                   VARCHAR(20) NOT NULL   -- 'running' | 'completed'
  selected_for_deployment  BOOLEAN DEFAULT FALSE  -- marks paper test winner
  configuration            JSONB NOT NULL         -- full stream config snapshot at run start
  notes                    TEXT
  created_at               TIMESTAMPTZ DEFAULT NOW()
```

### backtest.lots
Every unit of capital moving through a slot. The core analytical table.

```sql
  lot_id           BIGSERIAL PRIMARY KEY
  run_id           INTEGER NOT NULL REFERENCES backtest.runs
  model_id         INTEGER NOT NULL REFERENCES backtest.models
  stream_id        INTEGER NOT NULL REFERENCES backtest.streams
  slot_number      SMALLINT NOT NULL              -- 1 or 2
  lot_sequence     INTEGER NOT NULL               -- 1st lot this slot ever had, 2nd, etc.
  status           VARCHAR(10) NOT NULL           -- 'CASH' | 'OPEN' | 'CLOSED'
  opening_capital  NUMERIC(12,2) NOT NULL         -- USDC at start of this lot
  btc_quantity     NUMERIC(20,8)                  -- null if CASH
  entry_price      NUMERIC(12,2)                  -- null if CASH
  high_water_mark  NUMERIC(12,2)                  -- trailing stop ceiling, updated while OPEN
  exit_price       NUMERIC(12,2)                  -- null if CASH or OPEN
  closing_capital  NUMERIC(12,2)                  -- null until CLOSED
  realized_pnl     NUMERIC(12,2)                  -- closing_capital - opening_capital
  entry_reason     TEXT                           -- which signal triggered entry
  exit_reason      TEXT                           -- trailing stop hit, circuit breaker, etc.
  opened_at        TIMESTAMPTZ
  closed_at        TIMESTAMPTZ                    -- null if OPEN or CASH
```

**Lot state machine:**
```
CASH → OPEN → CLOSED → (new CASH lot with closing_capital as opening_capital)
```

Capital compounds within the slot. If a slot closes at $12, the next CASH lot opens at $12. If it drops to $7, the next starts at $7.

---

## live schema

Real money. Real Kraken orders. Precious — never touched carelessly.
Same structure as backtest but with Kraken order IDs and a link back to the paper test it came from.

### live.models
```sql
  model_id          SERIAL PRIMARY KEY
  model_version     INTEGER NOT NULL           -- matches backtest model_version
  description       TEXT
  deployed_at       TIMESTAMPTZ NOT NULL
  based_on_run_id   INTEGER NOT NULL           -- FK to backtest.runs (the winning paper test)
  status            VARCHAR(20) NOT NULL       -- 'active' | 'completed'
```

### live.streams
```sql
  stream_id       SERIAL PRIMARY KEY
  model_id        INTEGER NOT NULL REFERENCES live.models
  stream_name     VARCHAR(100) NOT NULL
  stream_version  VARCHAR(10) NOT NULL
  strategy_type   VARCHAR(50) NOT NULL
  parameters      JSONB NOT NULL
  slot_count      SMALLINT DEFAULT 2
```

### live.lots
```sql
  lot_id           BIGSERIAL PRIMARY KEY
  model_id         INTEGER NOT NULL REFERENCES live.models
  stream_id        INTEGER NOT NULL REFERENCES live.streams
  slot_number      SMALLINT NOT NULL
  lot_sequence     INTEGER NOT NULL
  status           VARCHAR(10) NOT NULL          -- 'CASH' | 'OPEN' | 'CLOSED'
  opening_capital  NUMERIC(12,2) NOT NULL
  btc_quantity     NUMERIC(20,8)
  entry_price      NUMERIC(12,2)
  entry_order_id   VARCHAR(50)                   -- Kraken order ID
  high_water_mark  NUMERIC(12,2)
  exit_price       NUMERIC(12,2)
  exit_order_id    VARCHAR(50)                   -- Kraken order ID
  closing_capital  NUMERIC(12,2)
  realized_pnl     NUMERIC(12,2)
  entry_reason     TEXT
  exit_reason      TEXT
  opened_at        TIMESTAMPTZ
  closed_at        TIMESTAMPTZ
```

---

## reporting schema

Views that union backtest and live data for cross-environment comparison. The analytics layer only queries reporting — it never hits backtest or live directly.

### reporting.all_lots (view)
Unions `backtest.lots` and `live.lots` with a source tag. Enables head-to-head comparison of any paper test against any live model across the same time period.

### reporting.model_performance (view)
Aggregated metrics per model/run: total return, annualized return, max drawdown, win rate, avg winner, avg loser, cash efficiency ratio, model grade.

### reporting.stream_performance (view)
Aggregated metrics per stream across all runs and models. Answers: which stream lineages (by name and version) consistently perform best?

---

## Key Design Decisions

- **Capital compounds within a slot** — opening_capital of each new lot = closing_capital of the previous one
- **Model→Stream→Slot hierarchy** — every lot traces back to exactly one model, one stream, one slot number. Slot number (1 or 2) is operational, not analytical — it just gives each stream two opportunities
- **Configuration snapshot** — `backtest.runs.configuration` stores a JSONB snapshot of the full stream config at run start. Protects historical run data if parameters are later changed in `backtest.streams`
- **`based_on_run_id`** — every live model deployment traces back to the specific paper test that earned it, creating a full audit chain from experiment to production
