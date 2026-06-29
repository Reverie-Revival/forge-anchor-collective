# Handoff

## Last session: 2026-06-28

---

## What was completed

### Bug fixes
- **Stream Tester metric colors:** All delta_color="inverse" bugs fixed — negative values now show red correctly across all performance and benchmark metrics.
- **Ending Balance delta:** Removed `$` prefix from delta string so Streamlit parses sign correctly.
- **Equity chart:** Line and fill now red when ending balance < starting balance.
- **Latest Run tab:** Fixed regression where unsaved runs weren't visible. Stream Tester now shows a "⏳ Latest Run" tab immediately after a run, before saving. Python 3.9 type hint bug also fixed.

### Model 1 — stream locking workflow established
- `backtest.streams` schema extended: added `locked_test_id`, `grade`, `locked_at`, `description`, `notes`.
- **Model 1 inserted** (`model_id=1`): "5 streams × 2 slots × $10 = $100. BTC/USD only. Limit orders. 0.25% maker fee."
- **Momentum Rider v1 locked** as `stream_id=1`, Grade 5, `locked_test_id=3` (Primary 17.8%). Plain-English description stored in DB and shown in sidebar.
- Stream Tester sidebar now shows: human-readable description from DB → locked grade badge → collapsible "Signal details" for the technical config.

### Dip Hunter v1 — iteration in progress (not locked)

Tested 4 configurations on Primary window (2019–2023). None are ready to lock.

| Config | Ann. Return | PF | Win Rate | Notes |
|---|---|---|---|---|
| Base spec (15m, no filter) | -81.6% | 0.55 | 26.5% | 1,720 trades — total wipeout |
| 1h candles, no filter | -82.4% | 0.38 | 24.4% | Still wipeout — timeframe wasn't the problem |
| 1h + 200 SMA above + RSI<30 + 5% trail | -16.4% | 0.31 | 28.9% | Better but avg loser too large |
| SMA Pullback (50 SMA, 1h, 200 above, 5% trail) | +3.3% | 1.01 | 33.2% | First profitable — but mirrors Momentum Rider |

**Key finding — SMA Pullback is the wrong direction:**
Both SMA Pullback and Momentum Rider require price above 200 SMA. They both fire in bull markets and both sit out bears. High correlation = no diversification value. Adding this stream would just be doubling down on the same bet.

**The right direction for Dip Hunter:**
Dip Hunter should fire in the conditions Momentum Rider explicitly excludes:
- MR excludes: F&G < 25, price below 200 SMA, RSI < 55
- Dip Hunter should target: F&G in fear/extreme fear — the exact moment MR goes quiet

True complementarity design:
- MR fires when: F&G > 25, above 200 SMA, RSI > 55 → bull momentum
- DH should fire when: F&G < 25, RSI oversold → fear bounce / panic snap-back
- They would **never fire at the same time** — genuine regime separation

---

## Current state

**Model 1 — in progress**

| # | Stream | Grade | Status |
|---|---|---|---|
| 1 | Momentum Rider v1 | 5 — Elite | Locked ✓ |
| 2 | Dip Hunter v1 | — | Iterating |
| 3 | Breakout Scout v1 | — | Not started |
| 4 | Steady Climber v1 | — | Not started |
| 5 | Surge Rider v1 | — | Not started |

**`backtest.stream_tests` — 5 rows (all Momentum Rider v1). No Dip Hunter rows saved yet.**

The SMA Pullback result is currently showing as "⏳ Latest Run" in Stream Tester (Dip Hunter v1 tab) but has NOT been saved — don't save it, it's the wrong approach.

---

## What's next

### 1. Dip Hunter v1 — test the "fear bounce" design

Run this config on Primary first:

```python
params = {
    'primary_timeframe': '1h',
    'core_signal': 'rsi_dip',
    'core_params': {
        'rsi_period': 14,
        'rsi_threshold': 35,
        'sma_period': 20,
        'dip_pct': 2.0
    },
    'filters': {
        'trend_context': None,   # NO 200 SMA filter — must fire in downtrends too
        'rsi': None,
        'volume': None,
        'atr_regime': None,
        'bollinger': None
    },
    'position': {
        'trailing_stop_pct': 3.0,
        'entry_order_type': 'limit',
        'entry_expiry_candles': 1,
    },
    'sentiment': {
        'fear_greed': {'min': None, 'max': 30}  # ONLY trade when F&G < 30 (fear zone)
    }
}
```

Key questions to answer from results:
- Does it fire more in 2022 bear / 2026 YTD? (It should)
- Is the win rate reasonable (target > 35%)?
- Does the equity curve look different from Momentum Rider? (It must)

Iterate from there — try F&G < 25 vs < 35, RSI < 30 vs < 35, trail 2.5% vs 3% vs 5%.

### 2. Once Dip Hunter is locked
Same workflow as MR: insert into `backtest.streams` (model_id=1), update spec file status, write plain-English description.

### Complementarity reminder
Momentum Rider thrives: bull market, F&G > 25, RSI > 55, above 200 SMA.
Dip Hunter must cover: bear market, extreme fear, F&G < 25, panic conditions.
They should never fire at the same time — that's the test of true complementarity.

---

## Files changed this session
- `src/app/stream_tester.py` — metric color fixes, latest run tab, sidebar description overhaul
- `src/data/schema.sql` — backtest.streams new columns
- `docs/architecture/database-schema.md` — schema doc updated
