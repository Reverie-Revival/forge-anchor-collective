# Breakout Scout v2

**Type:** Consolidation breakout — bull-regime only  
**Status:** Locked — Model 1  
**Upgraded from:** [Breakout Scout v1](breakout-scout-v1.md)  
**stream_id:** 3 | **locked_test_id:** 35 (Primary v2)

## What Changed from v1

v1 was locked against Primary v1 (2019–2023) and had never been stress-tested on the 2022+ regime. On Primary v2 it produced -1.8% annualized with a profit factor of 0.64 — winners getting cut too early and trade count too thin.

Key changes in v2:
- **Breakout lookback 48h → 24h** — the biggest unlock. 48h required breaking a 2-day range; 24h catches breakouts earlier with better reward/risk.
- **Trailing stop 5% → 10%** — lets genuine breakout continuation run instead of getting cut on normal volatility.
- **F&G floor 50 → 55** — counterintuitive, but combined with the shorter lookback, requiring greedy sentiment (≥55) filters noise and improves PF.
- **SMA 200 above filter added** — breakouts only when price is above the 200-period SMA. Gives BS a distinct bull-regime identity: it fires when the macro trend is up, sentiment is greedy, and the market coils before exploding. This is the opposite regime from DH (bear/fear).

What was ruled out:
- `max_hold` — unlike DH, BS winners need to run. Force-exiting at 5–14 days consistently destroyed returns.
- Loosening ATR filter — the ATR filter at 90% is the quality gate. Removing it flooded bad trades (75 trades, 33% WR, -11.7%).
- Bollinger squeeze threshold tuning — irrelevant. Loosening from 6% to 12% or removing it produced identical results. The ATR filter is doing all the gating.
- Scale_up (2 slots) — better for the planned staggered-slot redesign; current scale_up implementation enters both slots simultaneously which doesn't add value.

## Concept

Waits for BTC to compress into a tight range (low ATR, Bollinger squeeze) while sentiment is greedy and price is in a macro uptrend, then enters when price breaks above the 24-hour high with a strong conviction candle. BS fires in a completely different regime from both MR (sustained trend, any sentiment) and DH (extreme fear, deep drawdown).

## Signal

| Component | Value |
|---|---|
| Core | Close > highest high of last 24 candles (24h) |
| ATR filter | ATR(14) < 90% of 30-period average ATR |
| Bollinger squeeze | Bandwidth < 6% of price |
| Breakout candle | Body ≥ 40% of range, close in top 40% of range |
| SMA regime | Price above SMA(200) |
| Sentiment | Fear & Greed ≥ 55 (greedy) |
| Entry | Limit at breakout candle close |
| Expiry | Cancel if unfilled after 2 candles |
| Trail | 10% below highest close since entry |
| Timeframe | 1h |

## Validated Results (locked config, 1 slot × $10 lot)

| Window | Ann. Return | Trades | Win Rate | Max DD | Profit Factor |
|---|---|---|---|---|---|
| Primary v2 (2022–now) | **+11.6%** | 16 | 31% | -25.3% | 1.78 |
| Full History (2018–now) | **+25.1%** | 41 | 39% | -27.8% | 2.11 |
| Recent (2024–now) | **+16.6%** | 12 | 33% | -25.3% | 1.84 |
| 2026 YTD | 0 trades | — | — | — | — |
| Primary Window (2019–2023) | **+36.2%** | 26 | 42% | -27.8% | 2.54 |

2026 YTD = 0 trades is expected. BS requires F&G ≥ 55 + price above SMA 200 — conditions that haven't occurred in the current fear-dominated 2026 market. DH covers that regime.

## Complementarity

| Stream | Regime | Sentiment | Price Context |
|---|---|---|---|
| Momentum Rider v2 | Sustained trend | Any (F&G > 25) | EMA momentum |
| Dip Hunter v2 | Fear bounce | Extreme fear (F&G ≤ 20) | 25%+ drawdown |
| **Breakout Scout v2** | **Volatility expansion** | **Greedy (F&G ≥ 55)** | **Above SMA 200** |

No signal overlap — BS fires only when DH and MR are not firing. When all three run together, the max DD at model level compresses to -12.8% (Primary v2) — better than any individual stream's standalone DD.
