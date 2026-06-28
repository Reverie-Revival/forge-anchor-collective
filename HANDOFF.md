# Handoff

## Last session: 2026-06-27 / 2026-06-28

---

## What was completed

### Momentum Rider v1 — LOCKED

Best config confirmed across three date windows:

| Window | Period | Annualized | PF | Max DD |
|---|---|---|---|---|
| Primary | 2019 → 2023 | **17.8%** | 1.36 | -34.9% |
| Full History | 2017 → today | **19.5%** | 1.22 | -39.1% |
| Recent | 2026 YTD | -28.2% | — | -12.9% |

Grade 5 — Elite on two of three windows. Recent is a bear market year (BTC HODL = -54%) — expected behavior. Stream spec updated: `docs/specs/streams/momentum-rider-v1.md`.

**Locked params:** 20/50 EMA · 1h candles · RSI > 55 · 200 SMA above · F&G > 25 · 5% trailing stop.

Key insight: switching to 1h candles was the biggest single improvement. RSI > 55 and F&G > 25 each added meaningful lift. Greed ceiling (F&G < 80) hurt — Momentum Rider wants to trade in greedy phases.

### Stream Tester — major overhaul

- **Sidebar:** Stream dropdown (all 5 streams) + Test Run dropdown (numbered configs)
- **Tabs:** One tab per date window per config. Saved tabs show full charts. Pending (⏳) tabs show full charts + Save button, disappear on save.
- **Tab ordering:** by saved_at / file creation time — preserves the order runs were made
- **Pending pkl system:** `runs/pending_{hash}_{start}_{end}.pkl` — Claude saves these after running multi-window tests so user can view and save each from the tab
- **Per-test pkl:** `runs/{test_id}.pkl` — saved on every DB save, enables chart rendering on revisit
- **DB:** `stream_tests` rebuilt with clean column order; `run_number` + `window_name` added; IDs renumbered 1/2/3; `stream_registry` dropped (premature — FK wires when Model 1 is assembled)

### Data

- `market_data` updated to 2026-06-28
- `sentiment_data` current

---

## Current state

**stream_tests table has 3 saved rows for Momentum Rider v1:**
- #1 · Run 1 · Primary · 14.9% (RSI > 50, no F&G)
- #2 · Run 2 · Primary · 16.3% (RSI > 55, no F&G)
- #3 · Run 3 · Primary · 17.8% (RSI > 55, F&G > 25) ← LOCKED CONFIG

Full History and Recent for Run 3 are saved as **pending pkl files** in `src/app/runs/` — they show as ⏳ tabs in the stream tester. User needs to click Save on each tab to record them to the DB.

---

## What's next

1. **Save Full History and Recent tabs** for Run #3 from the stream tester (showing as ⏳ tabs — just click Save on each)
2. **Design Stream 2** — should complement Momentum Rider by doing well in bear markets / extreme fear / choppy ranges. Dip Hunter is the natural candidate. Start from `docs/specs/streams/dip-hunter-v1.md`.
3. Run the **condition analysis** before locking Dip Hunter: test it specifically on the periods where Momentum Rider struggled (2022 bear, 2026 YTD)
4. Repeat for streams 3–5 with same regime-coverage mindset

### Complementarity reminder

Momentum Rider thrives: bull market, neutral-to-greedy sentiment (F&G > 25), strong RSI.
Momentum Rider struggles: bear market, extreme fear, choppy sideways range.
Next streams must cover those gaps. See `memory/project_stream_design_philosophy.md`.

---

## Files changed this session

- `src/app/stream_tester.py` — full overhaul (tabs, pending runs, sidebar dropdowns, unique widget keys)
- `src/app/runs/3.pkl` — pkl for saved Run #3 Primary
- `src/app/runs/pending_*.pkl` — Full History + Recent pending (gitignored, must save from app)
- `src/data/schema.sql` — stream_tests updated with run_number/window_name
- `docs/architecture/database-schema.md` — schema doc updated
- `docs/specs/streams/momentum-rider-v1.md` — locked params + full results
- `.gitignore` — `src/app/runs/` excluded
