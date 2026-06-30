# Surge Rider v1

**Type:** Volume momentum  
**Status:** Placeholder — never built or tested. Not in Model 1.  

## Concept

Enters on genuine buying surges confirmed by volume. Price moves without volume are noise — when volume spikes alongside a bullish candle and RSI shows active momentum (not overbought), the move has conviction.

## How It Works

Volume 2.5× average confirms participation. A bullish candle (close > open) confirms direction. RSI between 45–70 filters two failure modes: below 45 means weak momentum, above 70 means the move is already exhausted. Tight 2% trail because surge moves are sharp and quick.

## Signal

| Component | Value |
|---|---|
| Core | Volume > 2.5× 20-period average |
| Direction filter | Close > Open |
| RSI filter | RSI(14) between 45 and 70 |
| Entry | Limit at signal candle close |
| Expiry | Cancel if unfilled after 1 candle |
| Trail | 2% below highest close since entry |

## Known Weaknesses

- 2% trail gets shaken out easily on volatile 15m candles
- High signal frequency in busy markets — fees add up
- Volume quality matters; Coinbase and Kraken may differ for same period

## Parameters

```json
{
  "core_signal": "volume_surge",
  "core_params": {
    "volume_avg_period": 20,
    "volume_multiplier": 2.5
  },
  "filters": {
    "trend_context": null,
    "rsi": { "period": 14, "min": 45, "max": 70 },
    "volume": { "avg_period": 20, "min_multiplier": 2.5 },
    "atr_regime": null,
    "bollinger": null
  },
  "regime": null,
  "timeframe_confirmation": null,
  "time_filters": null,
  "position": {
    "trailing_stop_pct": 2.0,
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
