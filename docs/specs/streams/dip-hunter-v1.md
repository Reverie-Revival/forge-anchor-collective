# Dip Hunter v1

**Type:** Mean reversion — fear bounce  
**Status:** Locked — Model 1 (stream_id=2, Grade 4 — Strong)

## Concept

Enters during extreme fear environments when BTC is in a meaningful drawdown and RSI recovers from oversold — catching the snap-back after panic selling exhausts. Designed to complement Momentum Rider: MR sits out when F&G < 25; DH fires when F&G < 20. Zero signal overlap by design.

## How It Works

Waits for RSI to cross back UP through the oversold threshold (recovery, not just the dip itself). Requires a bullish entry candle (close > prev close) to confirm the bounce is happening. The `drawdown_from_high` filter ensures price has dropped materially — prevents entries on shallow dips in neutral market conditions. F&G < 20 gates out everything except genuine fear environments.

## Signal

| Component | Value |
|---|---|
| Core | RSI(14) crosses up through 30 — prev RSI < 30, curr RSI ≥ 30 |
| Confirmation | Bullish candle: close > prev close |
| Drawdown | Price ≥ 25% below its 90-day high |
| Sentiment | F&G < 20 (Fear / Extreme Fear only) |
| Entry | Limit at signal candle close |
| Expiry | Cancel if unfilled after 1 candle |
| Trail | 7.5% below highest close since entry |
| Min hold | 48 candles (2 days on 1h — prevents stop-hunt exits) |

## Regime Complement

- Fires in: bear market, extreme fear, post-crash recovery windows
- Sits out: bull market (F&G > 20), shallow dips (< 25% drawdown), RSI not yet recovering
- MR fires in: F&G > 25, price above 200 SMA, RSI > 55 — structurally opposite regimes

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
        'drawdown_from_high': {'lookback_days': 90, 'min_drop_pct': 25.0},
    },
    'sentiment': {'fear_greed': {'max': 20}},
    'position': {
        'trailing_stop_pct': 7.5,
        'entry_order_type': 'limit',
        'entry_expiry_candles': 1,
        'min_hold_candles': 48,
    },
}
```

## Validation Results (v2 baseline)

| Window | Period | Trades | Ann. Return | Win Rate | PF | Max DD |
|---|---|---|---|---|---|---|
| Primary | 2019–2023 | 39 | +13.8% | — | — | — |
| Full History | 2018– | 80 | +7.0% | — | — | — |
| Recent | 2024– | 17 | +9.2% | — | — | — |
| 2026 YTD | 2026 | 11 | -19.1% | — | — | — |

2026 context: BTC was in a sustained bear with no clean fear-bounce cycles — expected behavior, not a strategy flaw. DH meaningfully outperformed BTC buy-and-hold (-54%) even in the loss year.

## Key Design Decisions

- **`rsi_recovery` over `rsi_dip`:** The original `rsi_dip` signal (RSI continuously below 35) produced 900+ trades and catastrophic losses — it catches falling knives. `rsi_recovery` fires exactly once per oversold episode (the candle where RSI crosses back up), entering the bounce rather than the dip.
- **`require_bullish_candle`:** Adds second confirmation (close > prev close on entry candle). Win rate improvement was meaningful on Full History.
- **`drawdown_from_high` 25% / 90 days:** Prevents entries on shallow dips. BTC dips 10-15% constantly; 25% is a real correction that warrants a bounce trade.
- **7.5% trail + 48 min hold:** Larger trail than MR because fear bounces are violent and need room to breathe. Min hold prevents immediate stop-out on post-entry volatility.
- **F&G < 20:** The unlock. Without this, DH fires in bull markets where RSI temporarily dips — exactly the wrong environment for mean reversion. With it, every trade happens in proven fear conditions.
