# Breakout Scout v1

**Type:** Consolidation breakout  
**Status:** Candidate — Model 1  

## Concept

Waits for BTC to compress into a tight range, then enters when price breaks above the top of that range. Explosive moves often follow quiet periods — this stream tries to catch them at the start.

## How It Works

ATR below 70% of its 30-period average confirms genuine consolidation. A close above the highest high of the last 48 candles (12 hours) is the breakout trigger. The wide 4% trail lets the move run.

## Signal

| Component | Value |
|---|---|
| Core | Close > highest high of last 48 candles |
| ATR filter | ATR(14) < 70% of 30-period average ATR |
| Entry | Limit at breakout candle close |
| Expiry | Cancel if unfilled after 2 candles |
| Trail | 4% below highest close since entry |

## Known Weaknesses

- Fakeouts still happen even with ATR filter — stop loss is the protection
- Wide 4% trail gives back meaningful profit on reversal
- Stays mostly idle in high-volatility regimes (ATR filter rarely triggers)

## Available Attributes for Iteration

These attributes are built and ready to tune on this stream:

| Attribute | Purpose | Notes |
|---|---|---|
| `atr_regime.min_consecutive_candles` | Confirm consolidation has persisted for N candles | Reduces entries after a single quiet candle |
| `bollinger.squeeze.max_bandwidth_pct` | Second compression signal — BB bands must be tight | Works alongside ATR; both must agree |
| `breakout_candle.body_ratio_min` | Body must be ≥ N% of candle range | Filters wick fakeouts — weak-bodied candles excluded |
| `breakout_candle.close_position_min` | Close must be in top N% of candle range | Confirms conviction on the breakout candle |
| `partial_exit` | Take X% off at Y% gain, trail the rest | Lock profit on the initial pop, let remainder run |
| `volume` | Require above-average volume on breakout candle | High-volume breakouts fail far less often |
| `sentiment.fear_greed` | Gate on market sentiment | e.g. only break out above F&G 40 (neutral or higher) |

## Starting Parameters

```json
{
  "core_signal": "range_breakout",
  "core_params": {
    "breakout_lookback": 48
  },
  "filters": {
    "trend_context": null,
    "rsi": null,
    "volume": null,
    "atr_regime": { "period": 14, "avg_period": 30, "max_pct_of_avg": 70 },
    "bollinger": null,
    "breakout_candle": null
  },
  "regime": null,
  "timeframe_confirmation": null,
  "time_filters": null,
  "position": {
    "trailing_stop_pct": 4.0,
    "entry_order_type": "limit",
    "entry_expiry_candles": 2,
    "partial_exit": null,
    "max_hold_candles": null,
    "min_hold_candles": null
  },
  "pause_rules": null,
  "sentiment": null,
  "on_chain": null
}
```
