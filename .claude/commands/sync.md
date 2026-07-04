# Sync Local Data

Brings local postgres up to date with the latest market candles and Fear & Greed sentiment. Run this at the start of any session where you'll be backtesting or tuning streams.

```bash
python -m src.data.downloader && python -m src.data.sentiment
```

Both are incremental — they check the latest timestamp/date already in the DB and fetch only what's missing. Safe to run at any time.
