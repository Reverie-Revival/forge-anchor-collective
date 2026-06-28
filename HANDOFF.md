# Handoff

## Last session: 2026-06-28

---

## What was completed

### Bug fixes — Stream Tester

- **Metric delta colors fixed:** `delta_color="inverse"` was causing negative values to display green. Switched all performance/benchmark metrics to `delta_color="normal"` so sign determines color naturally.
- **Ending Balance delta fixed:** removed `$` prefix from delta string so Streamlit can parse the sign correctly (was always showing green).
- **Equity chart fixed:** line and fill now use red (`#f87171`) when ending balance < starting balance, green otherwise.

### Model 1 assembly — started

- **`backtest.models`** — Model 1 inserted (model_id=1): "5 streams × 2 slots × $10 = $100. BTC/USD only. Limit orders. 0.25% maker fee."
- **`backtest.streams`** — migration added `locked_test_id`, `grade`, `locked_at`, `notes` columns. Momentum Rider v1 inserted as stream_id=1, Grade 5, locked_test_id=3 (Primary 17.8%).
- **`schema.sql` + `database-schema.md`** updated to reflect new columns.

### Decisions confirmed (no work needed)

- Fees ARE already baked into the backtester (0.25% maker per side) — no re-runs needed.
- Synthetic future data tables (bull/bear/stagnant/combo) — **decided against**. Real historical data covers all regimes needed.
- Pending ⏳ tabs (Full History + Recent for Run #3) are already saved to DB.

---

## Current state

**Model 1 — in progress**

| Slot | Stream | Grade | Status |
|---|---|---|---|
| 1 | Momentum Rider v1 | 5 — Elite | Locked ✓ |
| 2 | Dip Hunter v1 | — | Not started |
| 3 | Breakout Scout v1 | — | Not started |
| 4 | Steady Climber v1 | — | Not started |
| 5 | Surge Rider v1 | — | Not started |

**`backtest.stream_tests` — 5 saved rows for Momentum Rider v1:**
- #1 · Run 1 · Primary · 14.9%
- #2 · Run 2 · Primary · 16.3%
- #3 · Run 3 · Primary · 17.8% ← LOCKED
- #4 · Run 3 · Full History · 19.5%
- #5 · Run 3 · Recent · -28.2% (bear market — expected, beats BTC HODL -54%)

---

## What's next

1. **Design and test Stream 2 — Dip Hunter v1**
   - Spec at `docs/specs/streams/dip-hunter-v1.md` — read it before starting
   - Must complement Momentum Rider: perform in bear markets, extreme fear, choppy ranges
   - Run condition analysis first: test specifically on 2022 bear and 2026 YTD (where Momentum Rider struggled)
   - Same workflow: iterate in Claude Code → visual review in Streamlit → save → lock
2. Once locked, insert into `backtest.streams` (model_id=1, stream_id=2) — same process as Momentum Rider
3. Repeat for streams 3–5

### Complementarity reminder

Momentum Rider thrives: bull market, neutral-to-greedy sentiment (F&G > 25), strong RSI.
Momentum Rider struggles: bear market, extreme fear, choppy sideways range.
Dip Hunter must cover those gaps. See `memory/project_stream_design_philosophy.md`.

### Locking workflow (for reference)

When a stream is validated and locked:
1. Insert into `backtest.streams` with `model_id=1`, `locked_test_id=<Primary window test_id>`, `grade`, `notes`
2. Update `docs/specs/streams/<stream>.md` status to "Locked — Model 1 candidate"
3. Commit + push

---

## Files changed this session

- `src/app/stream_tester.py` — metric delta color fixes + equity chart color fix
- `src/data/schema.sql` — backtest.streams new columns (locked_test_id, grade, locked_at, notes)
- `docs/architecture/database-schema.md` — updated to reflect new columns
