"""
One-time deployment script for a live model.
Run once on the server before starting the executor.

Usage:
    python -m src.live.deploy

What it does:
  1. Creates a live.models row (Model 1, status=active)
  2. Copies the 3 locked streams from backtest.streams → live.streams at $33.33/lot
  3. Prints a confirmation table

Safe to inspect — will abort if model already deployed.
"""
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

BACKTEST_MODEL_ID = 1        # backtest.models row for Model 1
LIVE_MODEL_VERSION = 1
BASED_ON_MODEL_TEST_ID = 15  # Run #4, Primary v2 — the gate-passing test
LIVE_LOT_SIZE_USD = 33.33
DESCRIPTION = "Model 1 — MR v2 + DH v2 + BS v2 — equal $33.33/stream"


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def deploy():
    engine = _get_engine()
    with engine.begin() as conn:
        # Guard: don't deploy twice
        existing = conn.execute(
            text("SELECT model_id FROM live.models WHERE model_version = :v"),
            {"v": LIVE_MODEL_VERSION},
        ).fetchone()
        if existing:
            print(f"Model {LIVE_MODEL_VERSION} already deployed (live.models.model_id={existing[0]}). Nothing to do.")
            return

        # Create live.models row
        row = conn.execute(
            text("""
                INSERT INTO live.models
                    (model_version, description, deployed_at, based_on_model_test_id, status)
                VALUES (:ver, :desc, :now, :mt_id, 'active')
                RETURNING model_id
            """),
            {
                "ver": LIVE_MODEL_VERSION,
                "desc": DESCRIPTION,
                "now": datetime.now(timezone.utc),
                "mt_id": BASED_ON_MODEL_TEST_ID,
            },
        )
        live_model_id = row.scalar()
        print(f"Created live.models row: model_id={live_model_id}")

        # Pull locked streams from backtest
        streams = conn.execute(
            text("""
                SELECT stream_name, stream_version, strategy_type, parameters, slot_count, slot_mode
                FROM backtest.streams
                WHERE model_id = :mid
                ORDER BY stream_id
            """),
            {"mid": BACKTEST_MODEL_ID},
        ).fetchall()

        if not streams:
            raise RuntimeError(f"No streams found for backtest.models.model_id={BACKTEST_MODEL_ID}")

        print(f"\nDeploying {len(streams)} streams:\n")
        print(f"  {'Stream':<30} {'Ver':<6} {'Lot':>8}   {'Slots'}")
        print(f"  {'-'*55}")

        for s in streams:
            conn.execute(
                text("""
                    INSERT INTO live.streams
                        (model_id, stream_name, stream_version, strategy_type,
                         parameters, slot_count, slot_mode, lot_size_usd)
                    VALUES
                        (:mid, :name, :ver, :stype,
                         :params::jsonb, :slots, :mode, :lot)
                """),
                {
                    "mid":   live_model_id,
                    "name":  s.stream_name,
                    "ver":   s.stream_version,
                    "stype": s.strategy_type,
                    "params": json.dumps(s.parameters),
                    "slots": s.slot_count,
                    "mode":  s.slot_mode,
                    "lot":   LIVE_LOT_SIZE_USD,
                },
            )
            print(f"  {s.stream_name:<30} {s.stream_version:<6} ${LIVE_LOT_SIZE_USD:>7.2f}   {s.slot_count}")

        total = LIVE_LOT_SIZE_USD * len(streams)
        print(f"\n  Total capital: ${total:.2f}")
        print(f"\nModel 1 deployed. Start the executor to begin live trading.")
        print(f"  python -m src.live.executor")
        print(f"  python -m src.live.executor --dry-run   (no real orders)")


if __name__ == "__main__":
    deploy()
