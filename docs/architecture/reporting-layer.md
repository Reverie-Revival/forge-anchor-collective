# Reporting Layer

How backtest, paper test, and live data are unified for comparison.

---

## Design Principle

The analytics layer never queries `backtest` or `live` schemas directly. Everything goes through `reporting` views. This means:

- Comparison queries are simple — no schema-switching logic in application code
- Adding a new environment in the future doesn't break existing queries
- DBeaver dashboards always have one place to look

---

## Core Views

### reporting.all_lots

Unions `backtest.lots` and `live.lots` into a single queryable surface. Every lot gets a `source` tag and a `run_type` tag so you always know where data came from.

```sql
SELECT
  'backtest'          AS source,
  mt.run_type,        -- 'historical' or 'paper'
  mt.model_test_id,
  bl.model_id,
  bl.stream_id,
  bl.slot_number,
  bl.lot_sequence,
  bl.status,
  bl.opening_capital,
  bl.closing_capital,
  bl.realized_pnl,
  bl.entry_price,
  bl.exit_price,
  bl.opened_at,
  bl.closed_at,
  bl.entry_reason,
  bl.exit_reason
FROM backtest.lots bl
JOIN backtest.model_tests mt ON bl.model_test_id = mt.model_test_id

UNION ALL

SELECT
  'live'              AS source,
  'live'              AS run_type,
  NULL                AS model_test_id,
  ll.model_id,
  ll.stream_id,
  ll.slot_number,
  ll.lot_sequence,
  ll.status,
  ll.opening_capital,
  ll.closing_capital,
  ll.realized_pnl,
  ll.entry_price,
  ll.exit_price,
  ll.opened_at,
  ll.closed_at,
  ll.entry_reason,
  ll.exit_reason
FROM live.lots ll
```

---

### reporting.model_performance

Aggregated performance metrics per model and run. The scorecard view.

Columns: `source`, `run_type`, `model_test_id`, `model_version`, total return, annualized return, max drawdown, win rate, avg winner, avg loser, total trades, cash efficiency ratio, grade (1-5).

**Cash Efficiency Ratio** = Annualized Return ÷ Maximum Capital Deployed. A model using 55% of available capital to return 18% is more efficient than buy-and-hold using 100% for 20%.

---

### reporting.stream_performance

Aggregated metrics per stream name and version, across all models and run types.

Answers the question: which stream lineages consistently outperform? A stream that was "Momentum Rider v1" in Model 1, adjusted to "Momentum Rider v2" in Model 2, and carried forward unchanged in Model 3 can be tracked across its full history.

Columns: `stream_name`, `stream_version`, `source`, `model_version`, win rate, avg return per lot, total closed lots, avg holding period.

---

### reporting.benchmark_comparison

Compares every model run against the three benchmarks for the same time period.

| Column | Description |
|---|---|
| model_version | Which model |
| run_type | historical / paper / live |
| period_start / end | Time range of comparison |
| model_return | Realized return of this model over the period |
| btc_hold_return | What BTC buy-and-hold returned the same period |
| sp500_return | What S&P 500 returned the same period |
| cash_return | 0% (the "do nothing" baseline) |
| vs_sp500 | model_return − sp500_return |

S&P 500 and BTC buy-and-hold returns are stored in a separate reference table and joined here. They need to be populated manually or via a data feed for each period.

---

## Common Queries

**How is Model 1 live doing vs what the paper test predicted?**
```sql
SELECT source, run_type, model_version, annualized_return, max_drawdown
FROM reporting.model_performance
WHERE model_version = 1
ORDER BY source;
```

**Which stream has the best win rate across all runs?**
```sql
SELECT stream_name, stream_version, AVG(win_rate) as avg_win_rate
FROM reporting.stream_performance
GROUP BY stream_name, stream_version
ORDER BY avg_win_rate DESC;
```

**Is Model 2 Paper Test 3 beating Model 1 live over the same period?**
```sql
SELECT source, model_test_id, model_version, model_return, sp500_return, grade
FROM reporting.benchmark_comparison
WHERE model_version IN (1, 2)
AND period_start = '2026-01-01'
ORDER BY model_return DESC;
```
