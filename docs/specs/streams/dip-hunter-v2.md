# Dip Hunter v2

**Type:** Mean reversion — fear bounce  
**Status:** Locked — Model 1 (stream_id=2)  
**Upgraded from:** [Dip Hunter v1](dip-hunter-v1.md)

## What Changed from v1

- **Trailing stop 7.5% → 10%** — v1's 7.5% trail was getting stopped out too early on deep fear bounces that needed room to breathe. 10% lets genuine recovery moves run.
- **RSI max filter added (min 35)** — prevents entering when RSI is already climbing high (overbought territory would mean missing the bounce). Tightens entry quality.
- **Max hold 240 candles added** — force-exits positions that have been open 10 days at 1h. Prevents capital lockup in slow, grinding recoveries that haven't resolved.

## Concept

Enters during extreme fear environments when BTC is in a meaningful drawdown and RSI recovers from oversold — catching the snap-back after panic selling exhausts. Designed to complement Momentum Rider: MR sits out when F&G < 25; DH fires when F&G < 20. Zero signal overlap by design.

## Signal

| Component | Value |
|---|---|
| Core | RSI(14) crosses up through 30 — prev RSI < 30, curr RSI ≥ 30 |
| Confirmation | Bullish candle: close > prev close |
| RSI ceiling | RSI < 35 at entry (in recovery zone, not already overbought) |
| Drawdown | Price ≥ 25% below its 90-day high |
| Sentiment | F&G < 20 (Fear / Extreme Fear only) |
| Entry | Limit at signal candle close |
| Expiry | Cancel if unfilled after 1 candle |
| Trail | 10% below highest close since entry |
| Min hold | 48 candles (2 days — prevents stop-hunt exits) |
| Max hold | 240 candles (10 days — prevents capital lockup) |
| Timeframe | 1h |

## Locked Parameters

```python
params = {
    'primary_timeframe': '1h',
    'core_signal': 'rsi_recovery',
    'core_params': {
        'rsi_period': 14,
        'rsi_threshold': 30,
        'require_bullish_candle': True,
    },
    'filters': {
        'rsi': {'min': 35},
        'drawdown_from_high': {'lookback_days': 90, 'min_drop_pct': 25.0},
    },
    'sentiment': {'fear_greed': {'max': 20}},
    'position': {
        'trailing_stop_pct': 10.0,
        'entry_order_type': 'limit',
        'entry_expiry_candles': 1,
        'min_hold_candles': 48,
        'max_hold_candles': 240,
    },
}
```

## Validated Results (locked config, 1 slot × $10 lot)

| Window | Ann. Return | Trades | Win Rate | Max DD | Profit Factor |
|---|---|---|---|---|---|
| Primary v2 (2022–now) | **+11.7%** | — | — | — | — |
| Full History (2018–now) | **+12.1%** | — | — | — | — |
| Recent (2024–now) | **+10.5%** | — | — | — | — |
| 2026 YTD | **+53.0%** | — | — | — | — |

## Complementarity

| Stream | Regime | Sentiment | Price Context |
|---|---|---|---|
| **Dip Hunter v2** | **Fear bounce** | **Extreme fear (F&G ≤ 20)** | **25%+ drawdown from 90d high** |
| Momentum Rider v2 | Sustained trend | Any (F&G > 25) | EMA momentum, above SMA 200 |
| Breakout Scout v2 | Volatility expansion | Greedy (F&G ≥ 55) | Above SMA 200, squeeze |

DH fires in exactly the conditions when MR and BS sit out. 2026 YTD is DH's showcase: BTC in sustained fear/bear, DH +53% while BS has zero trades and MR is sidelined.
