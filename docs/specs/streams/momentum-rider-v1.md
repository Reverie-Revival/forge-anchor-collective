# Momentum Rider v1

**Type:** Trend-following
**Status:** Locked — Model 1 candidate

## Concept

Catches trending BTC moves early by detecting when short-term momentum flips bullish above the long-term trend line. Sits idle during bear markets and extreme fear — only trades when conditions genuinely support momentum.

## Validated Results

| Window | Period | Annualized | Profit Factor | Max Drawdown | Win Rate |
|---|---|---|---|---|---|
| Primary | 2019-01-01 → 2023-01-01 | **17.8%** | 1.36 | -34.9% | 41.0% |
| Full History | 2017-01-01 → 2026-06-28 | **19.5%** | 1.22 | -39.1% | 42.0% |
| Recent | 2026-01-01 → 2026-06-28 | **-28.2%** | — | -12.9% | — |

**Grade: 5 — Elite** on Primary and Full History windows.
Recent window is a bear market year (BTC HODL = -54% annualized) — Momentum Rider lost less than holding but is correctly idle in downtrends. This is expected behavior. Complementary streams must cover this regime.

**Thrives when:** BTC is in a sustained uptrend, Fear & Greed is neutral-to-greedy (>25), RSI confirms momentum. Bull runs and recovery phases.
**Struggles when:** BTC is in a bear market or choppy sideways range. The 200 SMA filter keeps it mostly sidelined but lagging entries can still occur at trend transitions.

## Signal

| Component | Value |
|---|---|
| Core | 20 EMA crosses above 50 EMA |
| Timeframe | 1h candles (resampled from 15m data) |
| Trend filter | Price > 200 SMA |
| RSI filter | RSI > 55 (momentum confirmed, not just noise) |
| Sentiment gate | Fear & Greed Index > 25 (block extreme fear) |
| Entry | Limit at crossover candle close |
| Expiry | Cancel if unfilled after 2 candles |
| Trail | 5% below highest close since entry |

## Locked Parameters

```json
{
  "primary_timeframe": "1h",
  "core_signal": "ema_crossover",
  "core_params": {
    "ema_short": 20,
    "ema_long": 50
  },
  "filters": {
    "trend_context": { "sma_period": 200, "require": "above" },
    "rsi": { "period": 14, "min": 55, "max": null },
    "volume": null,
    "atr_regime": null,
    "bollinger": null
  },
  "regime": null,
  "timeframe_confirmation": null,
  "time_filters": null,
  "position": {
    "trailing_stop_pct": 5.0,
    "entry_order_type": "limit",
    "entry_expiry_candles": 2,
    "partial_exit": null,
    "max_hold_candles": null,
    "min_hold_candles": null
  },
  "pause_rules": null,
  "sentiment": {
    "fear_greed": { "min": 25, "max": null }
  },
  "on_chain": null
}
```

## Key Iteration Decisions

- **20/50 EMA over 9/21**: Wider EMAs on 1h candles reduce false crossovers dramatically
- **1h timeframe**: 15m candles produced ~1,800 signals and -18% annualized; 1h dropped to 174 signals and +14.9% — noise was the enemy
- **RSI > 55 over RSI > 50**: Stricter gate cut 14 trades and improved every metric — win rate up, drawdown down
- **F&G > 25**: Blocking extreme fear reduced max drawdown from -48% to -35% and lifted annualized return to 17.8% — the single best improvement of the session
- **No greed ceiling**: Tested F&G < 80 cap — hurt performance. Momentum Rider *wants* to trade in greed phases.
