# Momentum Rider v1

**Type:** Trend-following  
**Status:** Candidate — Model 1  

## Concept

Catches trending BTC moves early by detecting when short-term momentum flips bullish. Only enters when price is already above the long-term trend line — no catching falling knives.

## How It Works

9 EMA crosses above 21 EMA signals that short-term momentum has turned up. The 200 SMA filter ensures this only fires during broader uptrends. Trail follows the move until it reverses.

## Signal

| Component | Value |
|---|---|
| Core | 9 EMA crosses above 21 EMA |
| Trend filter | Price > 200 SMA |
| Entry | Limit at crossover candle close |
| Expiry | Cancel if unfilled after 2 candles |
| Trail | 3% below highest close since entry |

## Known Weaknesses

- Lags on fast reversals — will give back some gain before trail fires
- Whipsaws in choppy, sideways markets
- Sits completely idle in extended bear markets (200 SMA filter)

## Parameters

```json
{
  "core_signal": "ema_crossover",
  "core_params": {
    "ema_short": 9,
    "ema_long": 21
  },
  "filters": {
    "trend_context": { "sma_period": 200, "require": "above" },
    "rsi": null,
    "volume": null,
    "atr_regime": null,
    "bollinger": null
  },
  "regime": null,
  "timeframe_confirmation": null,
  "time_filters": null,
  "position": {
    "trailing_stop_pct": 3.0,
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
