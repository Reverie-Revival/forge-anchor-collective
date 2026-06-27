import os
import time
import requests
import psycopg2
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# Coinbase — historical backfill only (2017 → present, one-time pull)
COINBASE_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
COINBASE_GRANULARITY = 900       # 15 minutes in seconds
COINBASE_CANDLES_PER_REQUEST = 300
COINBASE_WINDOW_SECONDS = COINBASE_GRANULARITY * COINBASE_CANDLES_PER_REQUEST

# Kraken — all incremental updates going forward
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_PAIR = "XBTUSD"
KRAKEN_INTERVAL = 15             # minutes

BACKFILL_START = datetime(2017, 1, 1, tzinfo=timezone.utc)

def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME", "forge_anchor"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD", ""),
    )

def fetch_candles_coinbase(start: datetime, end: datetime) -> list:
    """Fetch up to 300 candles from Coinbase for the given window.
    Returns list of [timestamp, low, high, open, close, volume] sorted oldest first.
    Used for historical backfill only.
    """
    response = requests.get(COINBASE_URL, params={
        "granularity": COINBASE_GRANULARITY,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }, timeout=30)
    response.raise_for_status()
    candles = response.json()
    return sorted(candles, key=lambda c: c[0])  # Coinbase returns newest first

def fetch_candles_kraken(since: datetime) -> tuple[list, int]:
    """Fetch up to 720 candles from Kraken since the given datetime.
    Returns (candles, last_timestamp) where candles are [timestamp, open, high, low, close, volume].
    Used for all incremental updates.
    """
    response = requests.get(KRAKEN_URL, params={
        "pair": KRAKEN_PAIR,
        "interval": KRAKEN_INTERVAL,
        "since": int(since.timestamp()),
    }, timeout=30)
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")

    result = data["result"]
    last_timestamp = result["last"]
    pair_key = [k for k in result if k != "last"][0]
    candles = result[pair_key]
    return candles, last_timestamp

def insert_candles_coinbase(conn, candles: list) -> int:
    """Insert Coinbase candles [timestamp, low, high, open, close, volume]. Returns count inserted."""
    inserted = 0
    with conn.cursor() as cur:
        for c in candles:
            timestamp = datetime.fromtimestamp(c[0], tz=timezone.utc)
            low, high, open_, close, volume = c[1], c[2], c[3], c[4], c[5]
            cur.execute("""
                INSERT INTO market_data (timestamp, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (timestamp) DO NOTHING
            """, (timestamp, open_, high, low, close, volume))
            if cur.rowcount:
                inserted += 1
    conn.commit()
    return inserted

def insert_candles_kraken(conn, candles: list) -> int:
    """Insert Kraken candles [timestamp, open, high, low, close, vwap, volume, count]. Returns count inserted."""
    inserted = 0
    with conn.cursor() as cur:
        for c in candles:
            timestamp = datetime.fromtimestamp(int(c[0]), tz=timezone.utc)
            open_, high, low, close, volume = c[1], c[2], c[3], c[4], c[6]
            cur.execute("""
                INSERT INTO market_data (timestamp, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (timestamp) DO NOTHING
            """, (timestamp, open_, high, low, close, volume))
            if cur.rowcount:
                inserted += 1
    conn.commit()
    return inserted

def get_latest_timestamp(conn) -> datetime:
    """Return the datetime of the most recent candle in the DB, or BACKFILL_START."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(timestamp) FROM market_data")
        result = cur.fetchone()[0]
    if result is None:
        return BACKFILL_START
    return result.replace(tzinfo=timezone.utc)

def run_backfill(conn):
    """Pull full history from Coinbase (2017 → now). One-time operation."""
    now = datetime.now(tz=timezone.utc)
    window_start = BACKFILL_START
    total_inserted = 0
    requests_made = 0

    print(f"Source: Coinbase Exchange (BTC-USD, 15m) | From: {window_start.date()}")
    print()

    while window_start < now:
        window_end = min(window_start + timedelta(seconds=COINBASE_WINDOW_SECONDS), now)
        try:
            candles = fetch_candles_coinbase(window_start, window_end)
            requests_made += 1
            if candles:
                inserted = insert_candles_coinbase(conn, candles)
                total_inserted += inserted
                progress_date = datetime.fromtimestamp(candles[-1][0], tz=timezone.utc).date()
                print(f"  {progress_date} — {inserted} new candles (request #{requests_made})")
            window_start = window_end
            time.sleep(0.4)
        except requests.RequestException as e:
            print(f"  Network error: {e} — retrying in 10s")
            time.sleep(10)

    return total_inserted, requests_made

KRAKEN_MAX_LOOKBACK_DAYS = 6  # Kraken stores ~7.5 days; stay safely under that

def run_incremental(conn):
    """Fetch candles newer than latest in DB.
    Uses Kraken if gap <= 6 days. Falls back to Coinbase first if gap is larger,
    then hands off to Kraken for the final stretch.
    """
    latest = get_latest_timestamp(conn)
    now = datetime.now(tz=timezone.utc)
    gap_days = (now - latest).total_seconds() / 86400
    total_inserted = 0
    requests_made = 0

    if gap_days > KRAKEN_MAX_LOOKBACK_DAYS:
        # Gap too large for Kraken — use Coinbase to catch up to within range, then Kraken
        coinbase_end = now - timedelta(days=KRAKEN_MAX_LOOKBACK_DAYS)
        print(f"Gap is {gap_days:.1f} days — Coinbase fills {latest.date()} → {coinbase_end.date()}, then Kraken takes over")
        print()
        window_start = latest
        while window_start < coinbase_end:
            window_end = min(window_start + timedelta(seconds=COINBASE_WINDOW_SECONDS), coinbase_end)
            try:
                candles = fetch_candles_coinbase(window_start, window_end)
                requests_made += 1
                if candles:
                    inserted = insert_candles_coinbase(conn, candles)
                    total_inserted += inserted
                    progress_date = datetime.fromtimestamp(candles[-1][0], tz=timezone.utc).date()
                    print(f"  [Coinbase] {progress_date} — {inserted} new candles")
                window_start = window_end
                time.sleep(0.4)
            except requests.RequestException as e:
                print(f"  Network error: {e} — retrying in 10s")
                time.sleep(10)
        latest = get_latest_timestamp(conn)

    # Kraken for the recent portion
    print(f"  [Kraken] picking up from {latest.date()}")
    since = latest
    while since < now:
        try:
            candles, last_timestamp = fetch_candles_kraken(since)
            requests_made += 1
            if candles:
                inserted = insert_candles_kraken(conn, candles)
                total_inserted += inserted
                progress_date = datetime.fromtimestamp(last_timestamp, tz=timezone.utc).date()
                print(f"  [Kraken] {progress_date} — {inserted} new candles (request #{requests_made})")
            next_since = datetime.fromtimestamp(last_timestamp, tz=timezone.utc)
            if next_since <= since:
                break
            since = next_since
            time.sleep(1.0)
        except requests.RequestException as e:
            print(f"  Network error: {e} — retrying in 10s")
            time.sleep(10)

    return total_inserted, requests_made

def run(mode: str = "auto"):
    """
    Modes:
      auto        — backfill if DB is empty, incremental (Kraken) if data exists
      backfill    — Coinbase full history pull from Jan 1 2017
      incremental — Kraken pull of candles newer than latest in DB
    """
    conn = get_db()
    latest = get_latest_timestamp(conn)
    is_empty = latest == BACKFILL_START

    if mode == "backfill" or (mode == "auto" and is_empty):
        print(f"Mode: backfill")
        total_inserted, requests_made = run_backfill(conn)
    else:
        print(f"Mode: incremental")
        total_inserted, requests_made = run_incremental(conn)

    conn.close()
    print(f"\nDone. {total_inserted:,} candles inserted across {requests_made:,} requests.")

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    run(mode)
