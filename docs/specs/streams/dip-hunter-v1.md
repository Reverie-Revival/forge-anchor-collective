# Dip Hunter v1

**Type:** Mean reversion  
**Status:** Candidate — Model 1  

## Concept

Buys short-term oversold dips expecting a snap-back to the mean. BTC regularly overcorrects on sell-offs — this stream tries to capture the bounce.

## How It Works

RSI below 35 signals oversold momentum. Requiring price to also be 2% below the 20 SMA confirms a real dip occurred, not just low momentum. The tight 2.5% trail captures the bounce without overstaying.

## Signal

| Component | Value |
|---|---|
| Core | RSI(14) < 35 AND close > 2% below 20 SMA |
| Entry | Limit at signal candle close |
| Expiry | Cancel if unfilled after 1 candle |
| Trail | 2.5% below highest close since entry |

## Known Weaknesses

- Catches falling knives in sustained downtrends (RSI stays oversold)
- 2.5% trail exits early if the dip becomes a full trend reversal
- High chop rate in sideways markets — many small losses

## Parameters

```json
{
  "core_signal": "rsi_dip",
  "core_params": {
    "rsi_period": 14,
    "rsi_threshold": 35,
    "sma_period": 20,
    "dip_pct": 2.0
  },
  "filters": {
    "trend_context": null,
    "rsi": null,
    "volume": null,
    "atr_regime": null,
    "bollinger": null
  },
  "regime": null,
  "timeframe_confirmation": null,
  "time_filters": null,
  "position": {
    "trailing_stop_pct": 2.5,
    "entry_order_type": "limit",
    "entry_expiry_candles": 1,
    "partial_exit": null,
    "max_hold_candles": null,
    "min_hold_candles": null
  },
  "pause_rules": null,
  "sentiment": null,
  "on_chain": null
}
```
