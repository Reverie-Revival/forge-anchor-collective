# Steady Climber v1

**Type:** Trend-filtered pullback  
**Status:** Candidate — Model 1  

## Concept

The most conservative stream in Model 1. Only operates during confirmed BTC uptrends and buys pullbacks to the 50 SMA rather than chasing price. Designed to produce fewer, higher-quality entries.

## How It Works

Price above 200 SMA confirms the uptrend. Price within 1.5% of the 50 SMA from above is the pullback. A candle closing higher than the previous confirms the bounce has started — no buying a falling touch. The 3.5% trail expects these trades to run longer than surge or dip trades.

## Signal

| Component | Value |
|---|---|
| Core | Pullback to 50 SMA in confirmed uptrend |
| Trend filter | Close > 200 SMA |
| Pullback condition | Close within 1.5% of 50 SMA from above |
| Bounce confirmation | Current close > previous close |
| Entry | Limit at 50 SMA value at signal time |
| Expiry | Cancel if unfilled after 3 candles |
| Trail | 3.5% below highest close since entry |

## Known Weaknesses

- Very selective — few signals, especially during volatile or bear markets
- Misses parabolic runs that never pull back to 50 SMA
- Sits completely idle during extended bear markets (200 SMA filter)

## Parameters

```json
{
  "core_signal": "sma_pullback",
  "core_params": {
    "trend_sma": 200,
    "pullback_sma": 50,
    "pullback_tolerance_pct": 1.5
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
    "trailing_stop_pct": 3.5,
    "entry_order_type": "limit",
    "entry_expiry_candles": 3,
    "partial_exit": null,
    "max_hold_candles": null,
    "min_hold_candles": null
  },
  "pause_rules": null,
  "sentiment": null,
  "on_chain": null
}
```
