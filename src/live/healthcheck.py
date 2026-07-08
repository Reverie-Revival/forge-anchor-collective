"""
Dead man's switch — run independently of the executor (separate cron-job.org schedule).
Queries live.executor_state and fires an alert if the executor hasn't run in > 2 hours.

Usage:
    python -m src.live.healthcheck
"""
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.live import notifier

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

ALERT_THRESHOLD_HOURS = 2


def run() -> None:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        log.error("DATABASE_URL not set")
        sys.exit(1)
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = create_engine(url)
    now = datetime.now(timezone.utc)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT last_run_at FROM live.executor_state WHERE id = 1")
        ).fetchone()

    if row is None:
        log.error("No executor_state row found — executor may never have run")
        notifier.alert_system_down(999)
        return

    last_run = row.last_run_at
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    gap_hours = (now - last_run).total_seconds() / 3600
    log.info(f"Last executor run: {last_run.strftime('%Y-%m-%d %H:%M UTC')} ({gap_hours:.1f}h ago)")

    if gap_hours > ALERT_THRESHOLD_HOURS:
        log.warning(f"Executor has been silent for {gap_hours:.1f}h — firing alert")
        notifier.alert_system_down(gap_hours)
    else:
        log.info("Executor heartbeat OK")


if __name__ == "__main__":
    run()
