"""
One-shot script: copies last 60 days of market_data + sentiment_data
from local Postgres to Supabase.

Usage:
    python -m src.data.seed_supabase

Requires both DATABASE_URL (local) and SUPABASE_DATABASE_URL set in .env
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

DAYS = 60

def get_local_conn():
    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME", "forge_anchor"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD", ""),
    )

def get_supabase_conn():
    url = os.getenv("SUPABASE_DATABASE_URL")
    if not url:
        sys.exit("SUPABASE_DATABASE_URL not set in .env")
    # psycopg2 needs postgresql:// not postgres://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)

def seed():
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)

    print(f"Connecting to local DB...")
    local = get_local_conn()

    print(f"Connecting to Supabase...")
    supa = get_supabase_conn()

    with local.cursor() as src, supa.cursor() as dst:
        # --- market_data ---
        print(f"Fetching market_data since {cutoff.date()}...")
        src.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM market_data WHERE timestamp >= %s ORDER BY timestamp",
            (cutoff,)
        )
        rows = src.fetchall()
        print(f"  {len(rows)} candles found")

        if rows:
            psycopg2.extras.execute_values(
                dst,
                """
                INSERT INTO market_data (timestamp, open, high, low, close, volume)
                VALUES %s
                ON CONFLICT (timestamp) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                    close=EXCLUDED.close, volume=EXCLUDED.volume
                """,
                rows,
                page_size=1000
            )
            print(f"  Upserted {len(rows)} candles to Supabase")

        # --- sentiment_data ---
        cutoff_date = cutoff.date()
        print(f"Fetching sentiment_data since {cutoff_date}...")
        src.execute(
            "SELECT date, fng_value, fng_label FROM sentiment_data WHERE date >= %s ORDER BY date",
            (cutoff_date,)
        )
        rows = src.fetchall()
        print(f"  {len(rows)} sentiment rows found")

        if rows:
            psycopg2.extras.execute_values(
                dst,
                """
                INSERT INTO sentiment_data (date, fng_value, fng_label)
                VALUES %s
                ON CONFLICT (date) DO UPDATE SET
                    fng_value=EXCLUDED.fng_value, fng_label=EXCLUDED.fng_label
                """,
                rows,
                page_size=500
            )
            print(f"  Upserted {len(rows)} sentiment rows to Supabase")

    supa.commit()
    local.close()
    supa.close()
    print("Done.")

if __name__ == "__main__":
    seed()
