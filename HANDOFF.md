# Handoff — 2026-07-03

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is deployed in dry-run mode.** GitHub Actions workflows are live and running. Everything works. The only step remaining before real money is setting `DRY_RUN` to `false` in GitHub Secrets.

### What's running on GitHub Actions
- **Market Data Updater** — every 15 min. Fetches from Kraken public OHLC since latest DB timestamp (self-heals gaps up to 7.5 days), upserts into Supabase `market_data`.
- **Live Executor** — every 30 min. Checks signals for streams whose candle timeframe closed, manages PENDING/OPEN lots, updates trailing stops, writes `last_run_at` to `live.executor_state`. With `DRY_RUN=false`, places real Kraken orders.

### Verified working this session
- Supabase connection via session pooler (IPv4 — direct connection is IPv6, incompatible with GitHub Actions)
- Schema applied, `live.models` + `live.streams` seeded (3 streams at $33.33/lot)
- `market_data` seeded and current through 2026-07-03
- `sentiment_data` seeded through 2026-07-03
- Signal checks clean for all 3 streams (no errors, no false signals)
- Dry-run executor tick logs cleanly: streams loaded, timeframe detection working

### To go live
1. GitHub repo → Settings → Secrets → set `DRY_RUN` to `false`
2. Confirm Kraken account has $100 USD balance (verified: $100.00 at deploy time)

---

## Bugs Fixed This Session

| File | Bug | Fix |
|---|---|---|
| `signal_engine.py` | `pd.Timestamp.utcnow()` is tz-aware in pandas 2.x; `market_data` index is tz-naive → crash on comparison | `.replace(tzinfo=None)` |
| `signal_engine.py` | Sentiment mapping used wrong pattern — broke key lookup vs `fng_map` | Match `engine.py`: `df.index.date` + `.map(fng_map)` |
| `market_data_updater.py` | Insert column named `ts` instead of `timestamp` | Renamed |
| `market_data_updater.py` | Kraken returns pair as `XXBTZUSD` not `XBTUSD` in response body | Fallback key lookup |
| `market_data_updater.py` | Fixed 2h lookback; gaps > 2h never self-healed | Fetch from latest DB timestamp |

---

## Prior Work — Streams + Model Assembly

### Streams (all v2, all locked)

**Momentum Rider v2** (stream_id=1, locked_test_id=26) — 4h | EMA 30/120 crossover | min_hold 48h | 7% trail
- Primary v2: +21.5% | Full History: +25.9% | Recent: +16.9%

**Dip Hunter v2** (stream_id=2, locked_test_id=34) — 1h | fear_dip | RSI min=35 | max_hold 240 candles | 10% trail
- Primary v2: +11.7% | Full History: +12.1% | Recent: +10.5%

**Breakout Scout v2** (stream_id=3, locked_test_id=35) — 1h | range_breakout lookback 24h | SMA 200 above | F&G ≥ 55 | 10% trail
- Primary v2: +11.6% | Full History: +25.1% | Recent: +16.6% | 2026 YTD: 0 trades (fear regime)

### Model Run #4 (all v2, equal $33.33/stream, $99.99 total)
| Window | Ann% | DD |
|---|---|---|
| Primary v2 (2022–) | +15.3% | -12.8% |
| Full History (2018–) | +22.2% | -15.8% |
| Recent (2024–) | +14.7% | -13.6% |
| 2026 YTD | +16.4% | -2.8% |
| Primary Window (2019–2023) | +30.7% | -13.9% |

---

## DB State

**backtest.streams (local Postgres only):**
- stream_id=1: Momentum Rider v2 — LOCKED (locked_test_id=26)
- stream_id=2: Dip Hunter v2 — LOCKED (locked_test_id=34)
- stream_id=3: Breakout Scout v2 — LOCKED (locked_test_id=35)

**backtest.model_tests (local Postgres only):**
| model_test_id | preset | ann% | DD |
|---|---|---|---|
| 15 | Primary v2 | +15.3% | -12.8% |
| 16 | Full History | +22.2% | -15.8% |
| 17 | Recent | +14.7% | -13.6% |
| 18 | 2026 YTD | +16.4% | -2.8% |
| 19 | Primary Window | +30.7% | -13.9% |

**live schema (Supabase):**
- `live.models`: model_id=1, Model 1, status=active
- `live.streams`: stream_ids 1/2/3, mirroring backtest streams at $33.33/lot each
- `live.executor_state`: id=1, last_run_at updated each tick

---

## Roadmap

| Priority | Item | Notes |
|---|---|---|
| **Now** | **Go live** | Set `DRY_RUN=false` in GitHub Secrets. Monitor Actions logs for first real signal + order. |
| **Next** | **Live Dashboard** | Streamlit monitoring — P&L, trade log, stream breakdown. Build once first trades occur. |
| — | Architecture & DB Remodel | Third full remodel. Scope after seeing live operation pain points. |
| — | Staggered Slot Redesign | Engine feature branch. Makes multi-slot streams useful (sequential vs. duplicate entries). DH + BS benefit most. |
| — | Build Model 2 | Start once Model 1 is stable. Overlap phase. |

---

## Architecture Reference

### src/live/ module (live-model-1 branch)
| File | Purpose |
|---|---|
| `kraken_client.py` | REST wrapper — place_order, cancel_order, get_order_status, get_balance, get_ticker_price |
| `deploy.py` | One-time script — creates live.models + live.streams rows. Already run against Supabase. |
| `signal_engine.py` | Checks latest candle for signal using same logic as backtester |
| `order_manager.py` | CASH→PENDING→OPEN→CLOSED state machine; limit entry + market exit |
| `position_monitor.py` | Checks trailing stops on OPEN lots at candle close |
| `executor.py` | Single-invocation tick (GitHub Actions); `--dry-run` skips Kraken calls, DB writes are real |
| `market_data_updater.py` | Fetches candles from Kraken public OHLC since latest DB timestamp, upserts to Supabase |

### GitHub Actions (workflows on both main + live-model-1)
| Workflow | Schedule | Key Secrets |
|---|---|---|
| `executor.yml` | every 30 min | SUPABASE_DATABASE_URL, KRAKEN_API_KEY, KRAKEN_API_SECRET, DRY_RUN |
| `market_data.yml` | every 15 min | SUPABASE_DATABASE_URL only |

### Supabase
- Account: personal Gmail (separate from Reverie Revival GitHub org — intentional)
- Project: forge-model-1, US East Ohio, free tier
- **Connection: Session Pooler URL required** — direct connection is IPv6, GitHub Actions is IPv4 only
- Schema: `src/data/supabase_schema.sql`
- Seed: `src/data/seed_supabase.py` (copies N days from local Postgres)

### Backtester (main branch, local only)
| File | Purpose |
|---|---|
| `src/backtester/engine.py` | Core engine — `_run_slot()`, `_warmup_days()`, `load_market_data()` |
| `src/backtester/signals.py` | Signal + filter logic |
| `src/backtester/model_runner.py` | Loads locked streams, runs model-level backtest |
| `src/app/db.py` | All DB ops |

**Run Streamlit:** `streamlit run src/app/app.py`

### Design Decisions Logged

**Staggered Slot Architecture (not yet built)**
Current slots are redundant (both enter same signal simultaneously). Intended design:
- Two independent capital buckets per stream
- Slot 2 must wait for the NEXT signal — no simultaneous entry
- Benefits DH (sequential fear dips) and BS (consecutive breakouts) most
- Engine-level change requiring a feature branch before touching `engine.py`
