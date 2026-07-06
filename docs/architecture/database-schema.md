# Database Schema — v3

**Database:** `forge_anchor` (PostgreSQL, local during development; Supabase for live)
**Schemas:** `backtest`, `live`, `reporting` + shared `market_data` / `sentiment_data` / `timeframe_presets`

> **Current status:** v3 architecture. Local postgres fully migrated. Supabase migration (`migration_v3.sql`) deferred until Model 2 development starts — live executor uses `live.*` schema only and is unaffected.
> Pre-v3 data is preserved in `backtest_bak.*` permanently.

---

## Shared Tables

### market_data
Raw BTC/USD price history. Lives outside both schemas — feeds everything.
Single interval: 15-minute candles from January 1, 2017 → present (~332,000 rows).

```
market_data
  candle_id   BIGSERIAL PRIMARY KEY
  timestamp   TIMESTAMPTZ NOT NULL UNIQUE
  open        NUMERIC(12,2) NOT NULL
  high        NUMERIC(12,2) NOT NULL
  low         NUMERIC(12,2) NOT NULL
  close       NUMERIC(12,2) NOT NULL
  volume      NUMERIC(20,8) NOT NULL
```

Index on `timestamp` — nearly every query filters by time range.

---

### sentiment_data
Daily Fear & Greed Index. Backfilled Feb 2018 → present.
Updated automatically via `market_data.yml` GitHub Actions workflow (runs `src.data.sentiment` every 15 min).

```
sentiment_data
  date       DATE PRIMARY KEY
  fng_value  SMALLINT NOT NULL     -- 0 (Extreme Fear) → 100 (Extreme Greed)
  fng_label  VARCHAR(30) NOT NULL
```

Candles before Feb 2018 skip the sentiment gate rather than blocking.

---

### timeframe_presets
Standard date windows referenced by stream tests and model tests. Replaces free-text window names — tests use a preset FK so the window definition is consistent and queryable.

```
timeframe_presets
  preset_id   SERIAL PRIMARY KEY
  name        VARCHAR(50) NOT NULL UNIQUE
  start_date  DATE NOT NULL
  end_date    DATE             -- NULL = open-ended (runs to present)
  description TEXT
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
  created_at  TIMESTAMPTZ DEFAULT NOW()
```

**Standard presets:**

| preset_id | Name | Start | End | Purpose |
|---|---|---|---|---|
| 1 | Primary Window | 2019-01-01 | 2023-12-31 | Legacy window. Use only for explicit cross-comparison with old results. |
| 2 | Full History | 2018-01-01 | open | All regimes including 2018 bear, COVID, 2021 mania |
| 3 | Recent | 2024-01-01 | open | Post-ETF approval regime only |
| 4 | 2026 YTD | 2026-01-01 | open | Current year (thin data — context only) |
| 5 | Primary v2 | 2022-01-01 | open | **Default for new builds.** Starts hard (2022 bear top). Modern cycle: crash → recovery → ETF bull → present. |

---

## backtest schema

All stream tuning and model validation lives here. Freely rebuilt during development. Never real money.

Pre-v3 data is snapshotted to `backtest_bak.*` and preserved permanently.

---

### backtest.streams — identity only

One row per named stream. Holds the identity (name, type) — NOT the configuration.
Configurations are versioned in `backtest.stream_configs`.

```
backtest.streams
  stream_id      SERIAL PRIMARY KEY
  stream_name    VARCHAR(100) NOT NULL UNIQUE   -- "Momentum Rider", "Dip Hunter", "Breakout Scout"
  strategy_type  VARCHAR(50) NOT NULL           -- "trend_following", "mean_reversion", "breakout"
  description    TEXT
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

---

### backtest.stream_configs — versioned configurations

One row per distinct parameter set. Version label is the version of that configuration: "v1", "v2", etc.
(Migrated data from pre-v3 uses labels like "v1r1", "v1r2" where multiple run_numbers existed for one version.)

Slot configuration lives here (and also in `parameters` JSONB under a `"slots"` key for full snapshot fidelity).

```
backtest.stream_configs
  stream_config_id  SERIAL PRIMARY KEY
  stream_id         INTEGER NOT NULL REFERENCES backtest.streams
  version           VARCHAR(20) NOT NULL          -- "v1", "v2", "v1r1"...
  parameters        JSONB NOT NULL                -- full strategy parameter snapshot
  slot_count        SMALLINT NOT NULL DEFAULT 1   -- max concurrent positions
  slot_mode         VARCHAR(30) NOT NULL DEFAULT 'single'
                    -- 'single' | 'staggered' | 'scale_down' | 'scale_up'
  notes             TEXT
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
  UNIQUE (stream_id, version)
```

**Slot modes:**
- `single` — one slot, one position at a time
- `staggered` — N independent slots consuming signals round-robin (longest-free slot gets next signal); enforces `slot_entry_gap_candles` between entries; supports `slot_capital_weight` for asymmetric sizing
- `scale_down` — slot 2 adds when price drops `slot2_trigger_pct` below slot 1's entry (DH pattern)
- `scale_up` — slot 2 adds when price rises `slot2_trigger_pct` above slot 1's entry and signal fires again (MR pattern)

---

### backtest.model_streams — model composition

Records which stream configs make up a model version, and at what capital allocation.
This is the single source of truth for "Model 1 used DH v2 at $33.33, MR v2 at $33.33, BS v2 at $33.33."

```
backtest.model_streams
  id                SERIAL PRIMARY KEY
  model_id          INTEGER NOT NULL REFERENCES backtest.models
  stream_config_id  INTEGER NOT NULL REFERENCES backtest.stream_configs
  lot_size_usd      NUMERIC(10,2) NOT NULL DEFAULT 10.00  -- $ per position; CHECK >= 10
  UNIQUE (model_id, stream_config_id)
```

---

### backtest.models — model version registry

One row per model version. The `model_version` field is the version number (1, 2, 3...).
A model row IS the config — it describes a specific composition of stream configs (see `model_streams`).

```
backtest.models
  model_id       SERIAL PRIMARY KEY
  model_version  INTEGER NOT NULL
  description    TEXT
  status         VARCHAR(20) NOT NULL DEFAULT 'active'
                 -- 'active' | 'completed' | 'archived'
  deployed_at    TIMESTAMPTZ
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

---

### backtest.stream_tests — individual stream tuning results

One saved result per (stream config × timeframe). Dedup key: `(stream_config_id, preset_id)` for preset runs, `(stream_config_id, custom_start, custom_end)` for custom. Re-running the same combo replaces the existing result (upsert).

```
backtest.stream_tests
  test_id               SERIAL PRIMARY KEY
  stream_config_id      INTEGER NOT NULL REFERENCES backtest.stream_configs  -- primary FK
  stream_name           VARCHAR(100) NOT NULL   -- denormalized for display (authoritative: stream_configs)
  stream_version        VARCHAR(20) NOT NULL
  run_number            INTEGER NOT NULL        -- legacy grouping field; always 1 for new rows
  preset_id             INTEGER REFERENCES timeframe_presets
  custom_start          TIMESTAMPTZ
  custom_end            TIMESTAMPTZ
  simulation_start      TIMESTAMPTZ             -- actual first candle after warmup clip
  simulation_end        TIMESTAMPTZ
  slot_count            SMALLINT NOT NULL DEFAULT 1
  slot_mode             VARCHAR(30) NOT NULL DEFAULT 'single'
  parameters            JSONB NOT NULL          -- snapshot of config at test time
  initial_capital       NUMERIC(10,2) NOT NULL
  ending_balance        NUMERIC(10,4)
  total_trades          INTEGER
  win_rate              NUMERIC(5,4)
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

A full pkl payload (trades + charts data) lives at `src/app/runs/{test_id}.pkl` for chart rendering.
`timeframe_label` is computed at query time: `COALESCE(preset.name, formatted_custom_dates)`.

---

### backtest.model_tests — full model backtests

All streams running together as a unit. Used to validate a model before live deployment.

```
backtest.model_tests
  model_test_id            SERIAL PRIMARY KEY
  model_id                 INTEGER NOT NULL REFERENCES backtest.models
  run_type                 VARCHAR(20) NOT NULL   -- 'historical' | 'paper'
  run_number               INTEGER
  preset_id                INTEGER REFERENCES timeframe_presets
  custom_start             TIMESTAMPTZ
  custom_end               TIMESTAMPTZ
  simulation_start         TIMESTAMPTZ NOT NULL
  simulation_end           TIMESTAMPTZ
  status                   VARCHAR(20) NOT NULL DEFAULT 'completed'  -- 'running' | 'completed'
  selected_for_deployment  BOOLEAN NOT NULL DEFAULT FALSE
  configuration            JSONB NOT NULL         -- {allocations: {stream: {lot_size_usd, ...}}}
  total_capital            NUMERIC(10,2)
  ending_balance           NUMERIC(10,4)
  total_trades             INTEGER
  win_rate                 NUMERIC(5,4)
  total_pnl                NUMERIC(10,4)
  total_return_pct         NUMERIC(8,2)
  annualized_return_pct    NUMERIC(8,2)
  max_drawdown_pct         NUMERIC(8,2)
  notes                    TEXT
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

Full pkl payload at `src/app/model_runs/{model_test_id}.pkl`.

---

### backtest.lots — per-trade capital state machine

Every unit of capital in a model test. Full trade-level record.

```
backtest.lots
  lot_id            BIGSERIAL PRIMARY KEY
  model_test_id     INTEGER NOT NULL REFERENCES backtest.model_tests
  model_id          INTEGER NOT NULL REFERENCES backtest.models
  stream_config_id  INTEGER NOT NULL REFERENCES backtest.stream_configs
  slot_number       SMALLINT NOT NULL
  lot_sequence      INTEGER NOT NULL        -- 1st lot this slot had, 2nd, etc.
  status            VARCHAR(10) NOT NULL    -- 'CASH' | 'OPEN' | 'CLOSED'
  opening_capital   NUMERIC(12,2) NOT NULL  -- compounds: closing_capital of previous lot
  btc_quantity      NUMERIC(20,8)
  entry_price       NUMERIC(12,2)
  high_water_mark   NUMERIC(12,2)           -- trailing stop ceiling, updated while OPEN
  exit_price        NUMERIC(12,2)
  closing_capital   NUMERIC(12,2)
  realized_pnl      NUMERIC(12,2)
  entry_reason      TEXT
  exit_reason       TEXT
  opened_at         TIMESTAMPTZ
  closed_at         TIMESTAMPTZ
```

State machine: `CASH → OPEN → CLOSED → (new CASH lot)`
Each slot has its own `high_water_mark` — trailing stops fire independently per slot.

---

## live schema

Real money. Real Kraken orders. Never touched carelessly.
Hosted on Supabase. Contains `live.*` + `public.market_data` (60 days) + `public.sentiment_data`.

The live schema is completely independent of the backtest schema.
Backtest schema migrations do NOT affect live — always verify before touching `live.*`.

**Safety rule:** `reset_backtest.sql` explicitly excludes all `live.*` tables.

```
live.models      -- one row per deployed model
live.streams     -- stream configs for the deployed model (copied from stream_configs at deploy time)
live.lots        -- per-trade record; mirrors backtest.lots + adds Kraken order IDs + PENDING status
live.executor_state   -- single-row last_run_at for stateless executor
live.executor_runs    -- one row per executor tick (for Live Monitor dashboard)
live.market_data_runs -- one row per market data fetch (for Live Monitor dashboard)
```

**live.lots adds vs backtest.lots:**
- Status includes `PENDING` (limit order placed, not yet filled)
- `entry_order_id`, `exit_order_id` — Kraken txid
- `entry_expiry_at` — when pending limit order auto-cancels

---

## reporting schema

Views only — never raw data. Analytics queries always go through reporting, never directly to backtest or live.

### reporting.all_lots (view)
Unions `backtest.lots` and `live.lots` with a `source` tag. Currently the only view built.

Additional views (`model_performance`, `stream_performance`, `benchmark_comparison`) are designed but not yet built — will be added when there's enough live data to make them useful.

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Stream identity separated from config | Eliminates duplicate dropdown entries ("MR v1", "MR v2") — app shows "Momentum Rider" and you pick the config version separately |
| `stream_config_id` is the dedup key | One result row per (config × timeframe) — re-running replaces, not duplicates |
| Model composition in `model_streams` | Explicit record of which configs make up a model at what allocation — survives future schema changes |
| Parameters snapshotted in both `stream_tests` and `stream_configs` | Historical tests always describe exactly what ran, even if the config is later updated |
| Capital compounds within a slot | `closing_capital` of each lot becomes `opening_capital` of the next — each slot is a self-contained compounding pool |
| `slot_mode` per stream config | Slot 2 behavior is strategy-specific — staggered for DH/BS, single for MR |
