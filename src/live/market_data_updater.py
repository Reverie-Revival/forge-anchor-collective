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
FALLBACK_LOOKBACK_HOURS = 2  # used only if DB has no data yet


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _latest_timestamp(engine) -> datetime:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT MAX(timestamp) FROM market_data")).fetchone()
    if row and row[0]:
        ts = row[0]
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(hours=FALLBACK_LOOKBACK_HOURS)


def fetch_recent_candles(since: datetime) -> list[dict]:
    response = requests.get(
        KRAKEN_URL,
        params={"pair": KRAKEN_PAIR, "interval": KRAKEN_INTERVAL, "since": int(since.timestamp())},
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")

    # Kraken normalizes pair name in response (XBTUSD → XXBTZUSD)
    result = data["result"]
    raw = result.get(KRAKEN_PAIR) or result.get("XXBTZUSD", [])
    now_ts = datetime.now(timezone.utc).timestamp()

    candles = []
    for row in raw:
        ts = int(row[0])
        # Drop in-progress candle (its close time hasn't arrived yet)
        if ts + KRAKEN_INTERVAL * 60 > now_ts:
            continue
        candles.append({
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
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
                INSERT INTO market_data (timestamp, open, high, low, close, volume)
                VALUES (:timestamp, :open, :high, :low, :close, :volume)
                ON CONFLICT (timestamp) DO UPDATE SET
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """),
            candles,
        )
    return result.rowcount


def _log_run(engine, candles_fetched: int, latest_candle, error: str = None) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM live.market_data_runs WHERE ran_at < now() - interval '90 days'"
        ))
        conn.execute(text("""
            INSERT INTO live.market_data_runs (candles_fetched, latest_candle, error)
            VALUES (:fetched, :latest, :error)
        """), {"fetched": candles_fetched, "latest": latest_candle, "error": error})


def run():
    log.info("=== Market Data Updater ===")
    engine = _get_engine()

    since = _latest_timestamp(engine)
    log.info(f"Fetching candles since {since.strftime('%Y-%m-%d %H:%M')} UTC")

    try:
        candles = fetch_recent_candles(since)
        log.info(f"Fetched {len(candles)} completed candles from Kraken")
        inserted = upsert_candles(engine, candles)
        log.info(f"Upserted {inserted} rows into market_data")
        latest = candles[-1]["timestamp"] if candles else since
        _log_run(engine, len(candles), latest)
    except Exception as e:
        log.error(f"Market data update failed: {e}")
        _log_run(engine, 0, None, error=str(e))
        raise

    log.info("=== Done ===")


if __name__ == "__main__":
    run()
