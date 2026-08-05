"""
Dead man's switch for Model 4 — run independently of the blended executor
(separate cron-job.org schedule). Queries live.executor_state for this
model's row (keyed by model_id, not the shared id=1 singleton Model 1 used)
and fires an alert if the executor hasn't run in > 2 hours.

Also runs the fee-drift check (src.live.fee_check). Every live model branch
keeps its OWN copy of MAKER_FEE/TAKER_FEE (in order_manager.py) -- these can
drift independently of each other, not just from Kraken's real rate (this
already happened once: live-model-3 ran with stale wrong constants for weeks
after live-model-1/main were corrected, discovered and fixed 2026-08-03).
Checking fee drift from only one model's healthcheck doesn't protect the
others' separate copies, so every model's healthcheck calls it.

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
from src.live.fee_check import check_fee_drift

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

ALERT_THRESHOLD_HOURS = 2
LIVE_MODEL_VERSION = 4


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
            log.error(f"No live.models row for model_version={LIVE_MODEL_VERSION} — run deploy_model4.py first")
            sys.exit(1)
        model_id = model_row.model_id

        row = conn.execute(
            text("SELECT last_run_at FROM live.executor_state WHERE model_id = :mid"),
            {"mid": model_id},
        ).fetchone()

    if row is None:
        log.error("No executor_state row found for Model 4 — executor may never have run")
        notifier.alert_system_down(999)
        return

    last_run = row.last_run_at
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    gap_hours = (now - last_run).total_seconds() / 3600
    log.info(f"Model 4 last executor run: {last_run.strftime('%Y-%m-%d %H:%M UTC')} ({gap_hours:.1f}h ago)")

    if gap_hours > ALERT_THRESHOLD_HOURS:
        log.warning(f"Model 4 executor has been silent for {gap_hours:.1f}h — firing alert")
        notifier.alert_system_down(gap_hours)
    else:
        log.info("Model 4 executor heartbeat OK")

    check_fee_drift()


if __name__ == "__main__":
    run()
