# ADR 006 — Market Data Strategy

**Date:** 2026-06-26
**Status:** Accepted

## Decision
Store 15-minute OHLCV candles for BTC/USD from January 1, 2017 to present, sourced from Kraken's public API. One interval, one table, no account required.

## Interval: 15 Minutes

### Why not 1 hour
1h was initially proposed on the assumption that strategies would be medium-term swing trades. That assumption pre-determines the strategy types before any testing has been done. The purpose of the backtesting system is to *discover* what works — locking to 1h locks out any faster strategy from being testable at all.

### Why not 1 minute or 5 minutes
The 0.50% round-trip fee on Kraken (0.25% maker per side) makes sub-15m scalping strategies unprofitable before they start. A strategy needs meaningful price movement to overcome fees. At 1m/5m granularity, the average candle move is too small — strategies would be fighting fees on every trade.

### Why 15 minutes
- Finest granularity where strategies can realistically overcome the fee hurdle
- Doesn't pre-determine what strategy types are worth testing
- More precise trailing stop simulation than 1h (uses candle HIGH/LOW to approximate intraday moves)
- More precise entry/exit signal detection — catches the start of a breakout rather than confirming it an hour late
- Still filters out microstructure noise of 1m/5m data
- Data volume is completely manageable (see below)

### The fee logic clarified
The 0.50% fee rules out **scalping** (close within 1-3 candles). It does not rule out swing strategies on 15m candles — those hold positions for hours or days, just with more granular signal detection. 15m is the correct floor, not a liability.

## History: January 1, 2017

### Why not earlier (2013-2016)
Pre-2017 BTC data reflects a fundamentally different market: Mt. Gox era dynamics, minimal institutional participation, illiquid order books, and price behavior driven by a tiny retail community. Those conditions will not repeat. Training on that data introduces noise that doesn't generalize to modern BTC markets.

### Why 2017
Starting from January 2017 captures three complete bull/bear cycles:

| Period | Market Condition |
|---|---|
| 2017 | Massive bull run |
| 2018 | -85% crash and bear market |
| 2019–2020 | Recovery + COVID crash + bounce |
| 2021 | All-time high bull run |
| 2022 | -75% bear market |
| 2023–2024 | Recovery + bull run |
| 2025–present | Current |

A model validated across all of these conditions is meaningfully more robust than one trained on a single market environment.

### Why not earlier than 2017 as optional data
Pre-2017 data can always be pulled separately for curiosity. It should not be included in the primary market_data table used for training — it would require filtering on every query and risks contaminating model training.

## Data Volume
- 15m candles from Jan 2017 to Jun 2026: ~9 years × 365 × 24 × 4 ≈ **315,000 rows**
- Single ticker (BTC/USD), single interval
- PostgreSQL handles this trivially — no partitioning or optimization needed at this scale

## Data Source
Coinbase Exchange public candles endpoint — no authentication required, no cost, real BTC-USD prices.
- Endpoint: `GET https://api.exchange.coinbase.com/products/BTC-USD/candles`
- Granularity: `900` (15 minutes in seconds)
- Pagination via `start` / `end` datetime window
- Returns up to 300 candles per request — requires ~1,100 requests to pull full history

### Why Coinbase Instead of Kraken or Binance
Kraken's public OHLC API only stores a rolling window of ~720 recent candles (~7.5 days at 15m). It cannot provide historical data going back to 2017. Binance has the historical data but geo-blocks US IP addresses (HTTP 451). Coinbase Exchange API is US-accessible, free, no auth required, and provides real BTC-USD prices (not USDT) going back to 2017.

## Ongoing Updates: Kraken
Once the Coinbase backfill is complete, all incremental updates use Kraken's public OHLC endpoint instead. Since incremental runs happen frequently (daily or more often), the latest DB timestamp will always fall within Kraken's ~7.5 day rolling window.

This keeps ongoing data on the exchange we trade on — price differences between Coinbase and Kraken are <0.1% and negligible for backtesting, but consistency with our execution venue is preferred for all forward data.

- Endpoint: `GET https://api.kraken.com/0/public/OHLC`
- Pair: `XBTUSD`
- Interval: `15` (minutes)
- Pagination via `since` Unix timestamp, returns up to 720 candles

## Ongoing Updates
The downloader runs in two modes:
1. **Backfill** — pulls full history from Jan 1 2017 on first run
2. **Incremental** — scheduled run (every 15 minutes or hourly) appends new candles

The same pipeline feeds backtesting and live trading. One data source throughout all phases.

## Consequences
- `market_data` table stores only 15m candles — no interval column needed, simplifying queries
- Train/validation/holdout split uses date ranges against this single table
- All backtests, paper tests, and live signal detection query the same table
- If a future strategy genuinely requires a different interval, the downloader is extended and a new table added — this is an explicit decision at that time, not a default
