# Handoff — 2026-07-04 (end of session)

---
## ⚠️ ACTION REQUIRED BY AUG 1, 2026 — ORACLE ACCOUNT
A tenancy deletion was submitted on an Oracle Cloud account (personal Gmail). Deletion takes 30 days.
**Before Aug 1:** Log into your credit card and confirm zero Oracle charges ever appeared. The account should be fully deleted by then — verify it's gone and no recurring relationship exists.
This reminder must stay at the top of every handoff until confirmed complete.
---

## Current State

**Model 1 is LIVE with its first open trade.**

- **Breakout Scout v2** entered at **$62,710.10** on 2026-07-03 21:00 UTC
- lot_id=2, stream_id=3, 0.00053149 BTC
- HWM = $62,710.10 (entry price — no run-up yet)
- 10% trailing stop → stop triggers if price drops to ~$56,439
- Position is OPEN in Supabase `live.lots`
- Backtester confirmed the same signal and entry — system is behaving correctly

## Done This Session

### Critical Bug Fix — Fill Detection
Kraken's `QueryOrders` silently returns `{}` for limit orders that fill immediately as taker trades. This caused the first live trade to be detected as expired and deleted (lots went empty despite a confirmed Kraken purchase). Fixed in `src/live/kraken_client.py`: `get_order_status()` now falls back to `TradesHistory` when `QueryOrders` returns empty. **Committed and pushed to live-model-1.**

### Lot Restoration
Manually re-inserted lot_id=2 into `live.lots` with correct values from Kraken `TradesHistory` data (txid=OF6YSN-4RZAY-LLJMSQ). lot_id is 2 not 1 because PostgreSQL SERIAL doesn't reset on row deletion.

### Sentiment Data Gap Fixed
`sentiment_data` in Supabase was stale (last date: 2026-06-28). The F&G ≥ 55 filter in Breakout Scout was silently doing nothing live (NaN comparison always passes). Fixed:
- Backfilled July 1–4 manually
- `src/data/sentiment.py` now incremental — checks DB max date, fetches only missing days (was fetching full 2000-row history every time, causing timeouts)
- Sentiment step added to `market_data.yml` GitHub Actions workflow — runs every 15 min, no-ops when already current, no separate cron-job.org job needed

### Run Logging (from earlier in session)
`executor.py` and `market_data_updater.py` now write one row per run to `live.executor_runs` and `live.market_data_runs`. 90-day retention enforced inline. Tables exist in Supabase and local postgres.

### Live Monitor Dashboard
Built `src/app/pages/live_monitor.py` on `feature/live-monitor` branch. Shows system status, open positions, executor/market_data run logs, closed trades. Registered in app.py nav. **Not yet merged to main — next session.**

### Branch Cleanup
Branches diverged badly during the session (feature work landed on live-model-1). Resolved: merged both directions, all conflicts resolved cleanly. As of session end:
- `live-model-1` and `main` are in sync (main is one merge-commit ahead)
- `feature/live-monitor` has the dashboard, one commit ahead of main

**Hard rule established going forward:** `live-model-1` is production. Only critical bug fixes go there. All other work on feature branches off `main`.

### Local DB Sync
Synced local postgres market_data to match Supabase (568 candles upserted). `DATABASE_URL` added explicitly to `.env`.

## Next Session — In Order

1. **Finish `feature/live-monitor`** — any remaining polish, then merge into main
2. **Set up session-start local sync habit** — run `python -m src.data.downloader` + `python -m src.data.sentiment` at the start of every session
3. **Watch the open Breakout Scout position** — confirm trailing stop monitoring is running correctly
4. **Reporting dashboard** — deferred until there are closed trades to verify with

## Open Questions

- Is the F&G ≥ 55 filter working correctly now that sentiment data is current? (Next signal from BS will be the real test)
- When to start Model 2 build? (Suggested: after Model 1 has a few weeks of clean live data)

---

## Reference: Architecture

### Branch Strategy (hard rule as of 2026-07-04)
- `main` — all development, workflow files, feature branches merge here
- `live-model-1` — production. GitHub Actions checks this out. **Critical fixes only. No feature work.**
- `feature/*` — new work, always branched from main, merged back to main
- Bug fixes to live: commit to `live-model-1` directly, cherry-pick to `main`

### GitHub Actions Workflows (all on main, workflow_dispatch only, triggered by cron-job.org)
| Workflow | Trigger | What It Does |
|---|---|---|
| `executor.yml` | Every 30 min | Runs `src.live.executor` tick |
| `market_data.yml` | Every 15 min | Fetches candles + updates sentiment (incremental) |

### Streams
- **Momentum Rider v2** (stream_id=1) — 4h \| EMA 30/120 \| 7% trail \| $33.33
- **Dip Hunter v2** (stream_id=2) — 1h \| fear_dip \| RSI≥35 \| 10% trail \| $33.33
- **Breakout Scout v2** (stream_id=3) — 1h \| range_breakout \| SMA200 \| F&G≥55 \| 10% trail \| $33.33

### Known Bugs Fixed (lifetime)
| File | Bug | Fix |
|---|---|---|
| `signal_engine.py` | tz-naive/aware mismatch | `.replace(tzinfo=None)` |
| `signal_engine.py` | Sentiment key lookup broken | Match `df.index.date` + `.map(fng_map)` |
| `market_data_updater.py` | Column named `ts` not `timestamp` | Renamed |
| `market_data_updater.py` | Kraken returns `XXBTZUSD` not `XBTUSD` | Fallback key lookup |
| `market_data_updater.py` | Fixed 2h lookback; gaps never self-healed | Fetch from latest DB timestamp |
| `executor.py` | tz-naive/aware in `_latest_candle_for_stream` | `.replace(tzinfo=None)` |
| `kraken_client.py` | `QueryOrders` returns `{}` for taker fills | Fall back to `TradesHistory` |
