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

FNG_URL = "https://api.alternative.me/fng/?limit={limit}&date_format=us"


def _get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor"))


def fetch_fng_history(limit: int = 0) -> list[dict]:
    """
    Fetch F&G history from alternative.me. limit=0 means full history.
    Returns list of {date, value, label} newest-first.
    """
    resp = requests.get(FNG_URL.format(limit=limit), timeout=15)
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


def _max_date_in_db():
    """Return the most recent date in sentiment_data, or None if empty."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM sentiment_data")
            return cur.fetchone()[0]
    finally:
        conn.close()


def run_update():
    """
    Incremental update: fetch only what's missing since the last DB entry.
    Falls back to full history fetch if the table is empty.
    """
    max_date = _max_date_in_db()
    today = date.today()

    if max_date is None:
        print("Table empty — fetching full history...")
        rows = fetch_fng_history(limit=0)
    else:
        days_behind = (today - max_date).days
        if days_behind <= 0:
            print(f"Already current ({max_date}). Nothing to do.")
            return
        # Fetch a few extra days as buffer in case of timezone edge cases
        limit = days_behind + 3
        print(f"DB is {days_behind} day(s) behind ({max_date}). Fetching last {limit} days...")
        rows = fetch_fng_history(limit=limit)

    if not rows:
        print("No data returned from API.")
        return

    print(f"  {len(rows)} records fetched ({rows[-1]['date']} → {rows[0]['date']})")
    n = upsert_rows(rows)
    print(f"  {n} rows upserted into sentiment_data")


if __name__ == "__main__":
    run_update()
