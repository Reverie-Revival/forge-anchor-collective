# ADR 003 — Trailing Stops Over Fixed Profit Targets

**Date:** 2026-06-26
**Status:** Accepted

## Decision
Use trailing stops as the sole exit mechanism. No fixed profit targets.

## Reasoning
- Fixed targets cap winners — a position that would have run 20% gets cut at 5%
- Trailing stops let the market decide when the trend is over, not the trader
- Percentage-based (not dollar-based) stops work at any BTC price level
- Consistent with the core insight: markets don't pay for activity, they pay for letting winners run

## Parameters (stream-specific, tuned during backtesting)
- **Initial stop:** percentage below entry price (e.g. 3-5%, varies by stream)
- **Trailing stop:** percentage below current high water mark (e.g. 4%)
- Tighter stops for shorter-term streams (Breakout), wider for longer-term (Trend Following)

## Consequences
- No guaranteed exit price — acceptable tradeoff for capturing full moves
- Requires careful tuning per stream to avoid premature stops in normal volatility
- Backtester must correctly implement the trailing high water mark logic to avoid lookahead bias
