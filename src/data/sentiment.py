"""
Fear & Greed Index pipeline.
Backfills full history and updates to today from the free alternative.me API.
Run directly: python -m src.data.sentiment
"""
import os
import requests
import psycopg2
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

FNG_URL = "https://api.alternative.me/fng/?limit=0&date_format=us"


def _get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor"))


def fetch_fng_history() -> list[dict]:
    """Fetch full F&G history from alternative.me. Returns list of {date, value, label}."""
    resp = requests.get(FNG_URL, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    rows = []
    for entry in data:
        try:
            dt = datetime.strptime(entry["timestamp"], "%m-%d-%Y").date()
            rows.append({
                "date": dt,
                "fng_value": int(entry["value"]),
                "fng_label": entry["value_classification"],
            })
        except (KeyError, ValueError):
            continue
    return rows


def upsert_rows(rows: list[dict]) -> int:
    """Insert or update rows in sentiment_data. Returns count inserted/updated."""
    if not rows:
        return 0
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO sentiment_data (date, fng_value, fng_label)
                VALUES (%(date)s, %(fng_value)s, %(fng_label)s)
                ON CONFLICT (date) DO UPDATE
                    SET fng_value = EXCLUDED.fng_value,
                        fng_label = EXCLUDED.fng_label
                """,
                rows,
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def load_sentiment(start: str = None, end: str = None) -> dict[date, int]:
    """Load F&G values from DB into a {date: fng_value} dict for backtester use."""
    conn = _get_conn()
    try:
        conditions = []
        if start:
            conditions.append(f"date >= '{start}'")
        if end:
            conditions.append(f"date < '{end}'")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        with conn.cursor() as cur:
            cur.execute(f"SELECT date, fng_value FROM sentiment_data{where}")
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def run_update():
    """Fetch and upsert full history (safe to run repeatedly — upserts on conflict)."""
    print("Fetching Fear & Greed history from alternative.me...")
    rows = fetch_fng_history()
    print(f"  {len(rows)} records fetched ({rows[-1]['date']} → {rows[0]['date']})")
    n = upsert_rows(rows)
    print(f"  {n} rows upserted into sentiment_data")


if __name__ == "__main__":
    run_update()
