"""
Market data updater — fetches the latest 15-min BTC/USD candles from Kraken
and upserts them into public.market_data.

Runs every 15 minutes via GitHub Actions cron. No Kraken auth required —
this uses the public OHLC endpoint.

Usage:
    python -m src.live.market_data_updater
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_PAIR = "XBTUSD"
KRAKEN_INTERVAL = 15  # minutes
FETCH_LOOKBACK_HOURS = 2  # fetch last 2 hours to ensure no gaps


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def fetch_recent_candles() -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=FETCH_LOOKBACK_HOURS)
    response = requests.get(
        KRAKEN_URL,
        params={"pair": KRAKEN_PAIR, "interval": KRAKEN_INTERVAL, "since": int(since.timestamp())},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")

    raw = data["result"].get(KRAKEN_PAIR, [])
    now_ts = datetime.now(timezone.utc).timestamp()

    candles = []
    for row in raw:
        ts = int(row[0])
        # Drop in-progress candle (its close time hasn't arrived yet)
        if ts + KRAKEN_INTERVAL * 60 > now_ts:
            continue
        candles.append({
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[6]),
        })

    return candles


def upsert_candles(engine, candles: list[dict]) -> int:
    if not candles:
        return 0
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO market_data (ts, open, high, low, close, volume)
                VALUES (:ts, :open, :high, :low, :close, :volume)
                ON CONFLICT (ts) DO UPDATE SET
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """),
            candles,
        )
    return result.rowcount


def run():
    log.info("=== Market Data Updater ===")
    engine = _get_engine()

    candles = fetch_recent_candles()
    log.info(f"Fetched {len(candles)} completed candles from Kraken")

    inserted = upsert_candles(engine, candles)
    log.info(f"Upserted {inserted} rows into market_data")

    log.info("=== Done ===")


if __name__ == "__main__":
    run()
