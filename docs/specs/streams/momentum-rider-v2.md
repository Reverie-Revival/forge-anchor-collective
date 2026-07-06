# Momentum Rider v2

**Type:** Trend-following  
**Status:** Locked — Model 1 (stream_id=1)  
**Upgraded from:** [Momentum Rider v1](momentum-rider-v1.md)

## What Changed from v1

- **Timeframe 1h → 4h** — the biggest unlock. 1h candles produced too many false crossovers in noise. 4h drops signal count dramatically and filters out intraday chop.
- **EMA 20/50 → 30/120** — wider EMAs scaled for the 4h timeframe. The wider gap means the crossover represents a more sustained momentum shift.
- **Trailing stop 5% → 7%** — 4h candles have larger intraday swings; 5% was getting stopped out on normal volatility within a continuing trend. 7% gives the trade room without sacrificing too much on exit.
- **Min hold 12 candles added** — prevents immediate exit on the candle immediately after entry (a common stop-hunt pattern on 4h).

## Concept

Catches trending BTC moves early by detecting when short-term momentum flips bullish above the long-term trend line. Operates on 4h candles to avoid intraday noise. Sits idle during bear markets and extreme fear — only trades when conditions genuinely support momentum continuation.

## Signal

| Component | Value |
|---|---|
| Core | 30 EMA crosses above 120 EMA |
| Trend filter | Price > SMA(200) |
| RSI filter | RSI(14) > 55 (momentum confirmed) |
| Sentiment gate | F&G > 25 (blocks extreme fear) |
| Entry | Limit at crossover candle close |
| Expiry | Cancel if unfilled after 2 candles |
| Trail | 7% below highest close since entry |
| Min hold | 12 candles (2 days on 4h) |
| Timeframe | 4h |

## Locked Parameters

```python
params = {
    'primary_timeframe': '4h',
    'core_signal': 'ema_crossover',
    'core_params': {
        'ema_short': 30,
        'ema_long': 120,
    },
    'filters': {
        'trend_context': {'sma_period': 200, 'require': 'above'},
        'rsi': {'period': 14, 'min': 55, 'max': None},
    },
    'sentiment': {'fear_greed': {'min': 25, 'max': None}},
    'position': {
        'trailing_stop_pct': 7.0,
        'entry_order_type': 'limit',
        'entry_expiry_candles': 2,
        'min_hold_candles': 12,
    },
}
```

## Validated Results (locked config, 1 slot × $10 lot)

| Window | Ann. Return | Trades | Win Rate | Max DD | Profit Factor |
|---|---|---|---|---|---|
| Primary v2 (2022–now) | **+21.5%** | — | — | — | — |
| Full History (2018–now) | **+25.9%** | — | — | — | — |
| Recent (2024–now) | **+16.9%** | — | — | — | — |
| 2026 YTD | — (0 trades) | 0 | — | — | — |

2026 YTD: 0 trades expected. BTC is in a fear-dominated bear market (F&G < 25 most of the year, price below SMA 200). MR correctly sits out. DH covers this regime.

## Complementarity

| Stream | Regime | Sentiment | Price Context |
|---|---|---|---|
| **Momentum Rider v2** | **Sustained trend** | **Any (F&G > 25)** | **EMA expanding, above SMA 200** |
| Dip Hunter v2 | Fear bounce | Extreme fear (F&G ≤ 20) | 25%+ drawdown from 90d high |
| Breakout Scout v2 | Volatility expansion | Greedy (F&G ≥ 55) | Above SMA 200, squeeze |

MR is the primary earner in bull markets. When BTC is trending up with expanding EMAs, MR runs while DH has no triggers and BS fires only on breakouts from consolidation.
