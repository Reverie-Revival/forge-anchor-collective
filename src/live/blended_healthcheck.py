"""
Dead man's switch for Model 3 — run independently of the blended executor
(separate cron-job.org schedule). Queries live.executor_state for Model 3's
row (keyed by model_id, not the shared id=1 singleton Model 1 uses) and fires
an alert if the executor hasn't run in > 2 hours.

Usage:
    python -m src.live.blended_healthcheck
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
LIVE_MODEL_VERSION = 3


def run() -> None:
    url = os.getenv("SUPABASE_DATABASE_URL", "")
    if not url:
        log.error("SUPABASE_DATABASE_URL not set — must point to Supabase, not local postgres")
        sys.exit(1)
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = create_engine(url)
    now = datetime.now(timezone.utc)

    with engine.connect() as conn:
        model_row = conn.execute(
            text("SELECT model_id FROM live.models WHERE model_version = :ver"),
            {"ver": LIVE_MODEL_VERSION},
        ).fetchone()
        if model_row is None:
            log.error(f"No live.models row for model_version={LIVE_MODEL_VERSION} — run deploy_model3.py first")
            sys.exit(1)
        model_id = model_row.model_id

        row = conn.execute(
            text("SELECT last_run_at FROM live.executor_state WHERE model_id = :mid"),
            {"mid": model_id},
        ).fetchone()

    if row is None:
        log.error("No executor_state row found for Model 3 — executor may never have run")
        notifier.alert_system_down(999)
        return

    last_run = row.last_run_at
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    gap_hours = (now - last_run).total_seconds() / 3600
    log.info(f"Model 3 last executor run: {last_run.strftime('%Y-%m-%d %H:%M UTC')} ({gap_hours:.1f}h ago)")

    if gap_hours > ALERT_THRESHOLD_HOURS:
        log.warning(f"Model 3 executor has been silent for {gap_hours:.1f}h — firing alert")
        notifier.alert_system_down(gap_hours)
    else:
        log.info("Model 3 executor heartbeat OK")


if __name__ == "__main__":
    run()
