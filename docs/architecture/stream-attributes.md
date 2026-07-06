# Stream Attribute System

Streams are composed of a **core signal** plus any number of **attributes**. The core signal defines what the stream is. Attributes shape when it fires, how it manages positions, and when it pauses.

Any stream can enable any attribute. Two streams can share the same core signal but behave completely differently based on their attribute configuration. This is how the tournament generates meaningful variation across models — not by reinventing signals, but by composing them differently.

Attributes are stored in the `parameters` JSONB column on `backtest.stream_configs` (and `live.streams` for deployed configs). A null value for any attribute group means that attribute is disabled for that stream.

---

## Availability Tiers

| Tier | Label | Meaning |
|---|---|---|
| ✅ | Available Now | Uses data we already have — no new pipeline needed |
| 🔜 | Planned — Free | Requires a free external source; build when ready |
| 💰 | Planned — Paid | Requires a paid API — not doing now |

---

## Attribute Groups

### 1. Core Signal ✅

Every stream has exactly one core signal. This is what the stream fundamentally does.

| Signal | Description |
|---|---|
| `ema_crossover` | Short EMA crosses above long EMA |
| `macd_crossover` | MACD line crosses above signal line — momentum confirmation entry |
| `rsi_recovery` | RSI crosses *back up* through threshold after being oversold — enters the bounce, not the fall |
| `rsi_dip` | RSI drops below threshold while price is below SMA — continuous oversold entry |
| `fear_dip` | Price drops N% below SMA (or prev candle) — no RSI required |
| `range_breakout` | Price breaks above N-candle high after consolidation |
| `volume_surge` | Volume spike + bullish candle + RSI in active range |
| `sma_pullback` | Pullback to SMA in confirmed uptrend |

New core signals can be added without changing the attribute system.

```json
"core_signal": "ema_crossover",
"core_params": {
  "ema_short": 9,
  "ema_long": 21
}
```

**MACD params:**
```json
"core_signal": "macd_crossover",
"core_params": {
  "macd_fast": 12,
  "macd_slow": 26,
  "macd_signal": 9,
  "require_growing_hist": false
}
```
`require_growing_hist`: if true, also requires the MACD histogram to be growing on the crossover candle (stricter confirmation).

---

### 2. Signal Filters ✅

Additional market data conditions that must be true on the same candle as the core signal. All enabled filters must pass — any failure blocks the entry.

| Filter | Description |
|---|---|
| `trend_context` | Price must be above or below a long SMA |
| `rsi` | RSI must fall within a min/max range |
| `drawdown_from_high` | Price must have dropped ≥ N% from its recent high — requires a real crash, not a routine dip |
| `volume` | Volume must exceed N× average |
| `atr_regime` | ATR must be below a threshold (% of average). `min_consecutive_candles` requires ATR to have been low for N consecutive candles — confirms genuine consolidation, not a one-candle dip in volatility |
| `bollinger` | Bollinger Band filters. `squeeze.max_bandwidth_pct`: BB bandwidth ((upper−lower)/mid×100) must be below this threshold — confirms price compression independent of ATR |
| `breakout_candle` | Quality check on the entry candle itself. `body_ratio_min`: candle body must be ≥ X fraction of total range (filters wick fakeouts). `close_position_min`: close must be in the top X fraction of the candle's range (confirms conviction) |

```json
"filters": {
  "trend_context": { "sma_period": 200, "require": "above" },
  "rsi": { "period": 14, "min": null, "max": 65 },
  "drawdown_from_high": { "lookback_days": 90, "min_drop_pct": 25.0 },
  "volume": { "avg_period": 20, "min_multiplier": 1.5 },
  "atr_regime": { "period": 14, "avg_period": 30, "max_pct_of_avg": 70, "min_consecutive_candles": 6 },
  "bollinger": { "period": 20, "std_dev": 2.0, "squeeze": { "max_bandwidth_pct": 6.0 } },
  "breakout_candle": { "body_ratio_min": 0.4, "close_position_min": 0.6 }
}
```

---

### 3. Trend / Regime Context ✅

Sets what broader market state must exist for the stream to be active at all. Different from signal filters — these define the operating regime, not just the entry condition.

| Attribute | Description |
|---|---|
| `volatility_regime` | Only trade in low / normal / high volatility environments |
| `bull_bear` | Only trade when price is above/below long-term SMA (daily equivalent) |
| `atr_floor` | Minimum ATR required — prevents trading in dead, zero-movement periods |

```json
"regime": {
  "volatility_regime": "low_to_normal",
  "bull_bear": "bull_only",
  "atr_floor": null
}
```

---

### 4. Primary Timeframe ✅

By default, every stream evaluates signals on 15m candles. Setting `primary_timeframe` resamples the raw 15m data up to a coarser interval before computing indicators and signals — the stream effectively runs on that timeframe.

| Value | Description |
|---|---|
| `"1h"` | Resample to 1-hour candles (4 × 15m) |
| `"4h"` | Resample to 4-hour candles (16 × 15m) |
| `"1d"` | Resample to daily candles (96 × 15m) |

Use this to reduce noise for strategies where 15m chop causes too many false signals. The underlying 15m data is preserved in the database — resampling happens at backtest time only.

```json
"primary_timeframe": "1h"
```

If omitted or null, the stream runs on 15m candles as before.

---

### 5. Time Filters ✅

Control when a stream is allowed to trade based on time of day or day of week.

| Filter | Description |
|---|---|
| `session_window` | Only trade between two UTC hours |
| `day_of_week` | Include or exclude specific days (0=Mon, 6=Sun) |
| `cooldown_candles` | Minimum candles between trades for this stream |

```json
"time_filters": {
  "session_window": { "start_hour_utc": 8, "end_hour_utc": 22 },
  "day_of_week": { "exclude": [6] },
  "cooldown_candles": 4
}
```

---

### 6. Position Management ✅

Controls how entries are made and exits are managed.

| Attribute | Description |
|---|---|
| `stop_loss_pct` | Hard floor N% below entry price — never moves. Caps your maximum loss per trade regardless of what price does after. Independent of trailing stop. |
| `trailing_stop_pct` | Trails N% below the highest close since entry — protects profits as price rises. Use this or ATR stop — not both. |
| `trailing_stop_atr_multiplier` | Trail stop N × ATR below highest close — volatility-adaptive. Widens in volatile markets, tightens in calm ones. Use with `trailing_stop_atr_period` (default 14). |
| `trailing_stop_atr_period` | ATR lookback period used when `trailing_stop_atr_multiplier` is set (default 14) |

Both `stop_loss_pct` and `trailing_stop_pct` can be set simultaneously. The engine checks both every candle and exits at whichever triggers first (the higher price). Example: `stop_loss_pct: 3, trailing_stop_pct: 7` — you lose at most 3% from entry, but once profitable the trailing stop lets winners run and only exits if price reverses 7% from peak.
| `entry_order_type` | `limit` only (per system constraint) |
| `entry_expiry_candles` | Cancel unfilled entry after N candles |
| `partial_exit` | Take X% off position at Y% gain, trail the rest — fully implemented, use freely |
| `max_hold_candles` | Force exit after N candles regardless of trailing stop |
| `min_hold_candles` | Don't exit within N candles even if stop triggers |

```json
"position": {
  "trailing_stop_pct": 3.0,
  "entry_order_type": "limit",
  "entry_expiry_candles": 2,
  "partial_exit": { "at_gain_pct": 5.0, "exit_pct": 50 },
  "max_hold_candles": null,
  "min_hold_candles": 2
}
```

ATR stop alternative:
```json
"position": {
  "trailing_stop_atr_multiplier": 2.5,
  "trailing_stop_atr_period": 14,
  "entry_order_type": "limit",
  "entry_expiry_candles": 2
}
```

---

### 7. Pause Rules ✅

Conditions that suspend a stream entirely until the condition clears. Different from filters — these don't block individual trades, they shut the stream down for a period.

| Rule | Description |
|---|---|
| `max_drawdown` | Pause if stream is down X% over Y days |
| `btc_crash_guard` | Pause if BTC drops more than X% in last 24h |
| `consecutive_losses` | Pause after N consecutive losing trades |

```json
"pause_rules": {
  "max_drawdown": { "pct": 20, "window_days": 7 },
  "btc_crash_guard": { "drop_pct": 10, "lookback_hours": 24 },
  "consecutive_losses": { "count": 4 }
}
```

---

### 8. Sentiment Layer ✅

Conditions based on external sentiment data. Any stream can add a sentiment gate — it doesn't change the core signal, just adds an additional filter.

**Fear & Greed Index** — [alternative.me/fng](https://api.alternative.me/fng/) — free, no auth, updates daily.
- Values: 0 (Extreme Fear) → 100 (Extreme Greed)
- History: Feb 2018 → present, stored in `sentiment_data` table
- Candles before Feb 2018 skip the sentiment gate (treated as no restriction)
- Example use: Dip Hunter only enters when F&G < 35 (confirms panic, not just a dip)
- Example use: Breakout Scout only enters when F&G > 55 (momentum-confirmed breakout)
- Run `python -m src.data.sentiment` to update to today

```json
"sentiment": {
  "fear_greed": { "min": null, "max": 35 }
}
```

Future free sources (not yet built):
- Reddit mention volume (r/Bitcoin, r/CryptoCurrency) — public API, rate-limited

---

### 9. On-Chain Layer 💰

Exchange inflow/outflow, whale movement, miner activity. Requires paid services (Glassnode, Nansen). Not building now — attribute schema reserved for future use.

```json
"on_chain": null
```

---

### 9. Slot Position ✅

Controls how capital is deployed across multiple independent slots within a stream. Each slot maintains its own open position, trailing stop, and capital independently. All slot configuration lives under a `"slots"` key in `parameters`.

| Attribute | Applies To | Description |
|---|---|---|
| `slot_count` | all modes | Number of independent slots (1–3) |
| `slot_mode` | all modes | `single` · `staggered` · `scale_down` · `scale_up` |
| `slot_entry_gap_candles` | staggered | Minimum candles between any two slot entries — prevents rapid stacking |
| `slot2_trigger_pct` | scale modes | Price must move this % from slot 1's entry before slot 2 fires |
| `slot_capital_weight` | multi-slot | Capital split across slots, e.g. `[70, 30]` (sums to 100). Default: equal split. |

**Slot modes:**
- `single` — one slot, one position at a time; slot_count ignored
- `staggered` — N slots consume signals round-robin; the slot that has been free longest gets the next signal; `slot_entry_gap_candles` enforces a minimum gap between any two entries
- `scale_down` — slot 2 enters when price drops `slot2_trigger_pct` below slot 1's entry (DH: average down)
- `scale_up` — slot 2 enters when price rises `slot2_trigger_pct` and signal fires again (MR: pyramid up)

```json
"slots": {
  "slot_count": 2,
  "slot_mode": "staggered",
  "slot_entry_gap_candles": 4,
  "slot_capital_weight": [70, 30]
}
```

---

## Full Parameter Schema

The complete structure every stream's `parameters` column should conform to:

```json
{
  "core_signal": "ema_crossover",
  "core_params": {},
  "filters": {
    "trend_context": null,
    "rsi": null,
    "drawdown_from_high": null,
    "volume": null,
    "atr_regime": null,
    "bollinger": null,
    "breakout_candle": null
  },
  "regime": {
    "volatility_regime": null,
    "bull_bear": null,
    "atr_floor": null
  },
  "timeframe_confirmation": null,
  "time_filters": null,
  "position": {
    "stop_loss_pct": null,
    "trailing_stop_pct": 3.0,
    "trailing_stop_atr_multiplier": null,
    "trailing_stop_atr_period": 14,
    "entry_order_type": "limit",
    "entry_expiry_candles": 2,
    "partial_exit": null,
    "max_hold_candles": null,
    "min_hold_candles": null
  },
  "pause_rules": null,
  "sentiment": null,
  "on_chain": null,
  "slots": {
    "slot_count": 1,
    "slot_mode": "single",
    "slot_entry_gap_candles": 0,
    "slot2_trigger_pct": null,
    "slot_capital_weight": null
  }
}
```

---

## How Attributes Evolve

- Model 1 has access to all ✅ attributes
- Fear & Greed sentiment is live — backfilled Feb 2018 → present in `sentiment_data`
- On-chain (💰) gets added if/when it makes sense to pay for it
- New attribute types can always be added to the schema — old streams are unaffected (their `null` values mean "disabled")

The backtester reads the full parameter schema and skips any null attribute group, so adding new attributes never breaks existing stream configurations.
