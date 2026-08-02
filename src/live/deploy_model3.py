"""
One-time deployment script for Model 3 (Grid Stacker Blended).
Run once on Supabase before starting the blended executor.

Usage:
    python -m src.live.deploy_model3

What it does:
  1. Creates a live.models row (model_version=3, status=active)
  2. Copies the locked stream config (backtest.stream_configs.stream_config_id=36)
     into live.streams
  3. Seeds live.blended_capital at $100 -- Model 3's own tracked capital,
     independent of Kraken's actual account balance and independent of
     Model 1's $100
  4. Prints a confirmation table

Safe to inspect -- will abort if Model 3 is already deployed.
"""
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

LIVE_MODEL_VERSION = 3
BACKTEST_STREAM_CONFIG_ID = 36    # Grid Stacker Blended v8 -- the locked, validated config
BASED_ON_MODEL_TEST_ID = 106      # backtest.model_tests row from src.backtester.finalize_model3,
                                   # local Postgres only (Supabase has no backtest schema --
                                   # same soft-reference pattern as Model 1's deploy.py; not a
                                   # real cross-database FK). Full History, +84.7% ann, 482 trades.
LIVE_STARTING_CAPITAL = 100.00
DESCRIPTION = "Model 3 — Grid Stacker Blended v8 — solo stream, $100, compounding"


def _get_supabase_engine():
    url = os.getenv("SUPABASE_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "SUPABASE_DATABASE_URL is not set. "
            "This must point to Supabase. Do not use DATABASE_URL for live deployment."
        )
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _get_local_engine():
    # backtest.stream_configs only exists on local Postgres -- Supabase has no
    # backtest schema at all (live schema + market_data + sentiment_data only).
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def deploy():
    local_engine = _get_local_engine()
    with local_engine.connect() as lconn:
        config = lconn.execute(
            text("""
                SELECT sc.stream_config_id, s.stream_name, sc.version, s.strategy_type,
                       sc.parameters, sc.slot_count, sc.slot_mode
                FROM backtest.stream_configs sc
                JOIN backtest.streams s ON s.stream_id = sc.stream_id
                WHERE sc.stream_config_id = :cid
            """),
            {"cid": BACKTEST_STREAM_CONFIG_ID},
        ).fetchone()
        if config is None:
            raise RuntimeError(f"No backtest.stream_configs row for stream_config_id={BACKTEST_STREAM_CONFIG_ID}")

    engine = _get_supabase_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT model_id FROM live.models WHERE model_version = :v"),
            {"v": LIVE_MODEL_VERSION},
        ).fetchone()
        if existing:
            print(f"Model {LIVE_MODEL_VERSION} already deployed (live.models.model_id={existing[0]}). Nothing to do.")
            return

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

        conn.execute(
            text("""
                INSERT INTO live.streams
                    (model_id, stream_name, stream_version, strategy_type,
                     parameters, slot_count, slot_mode, lot_size_usd)
                VALUES
                    (:mid, :name, :ver, :stype,
                     CAST(:params AS jsonb), :slots, :mode, :lot)
            """),
            {
                "mid":   live_model_id,
                "name":  config.stream_name,
                "ver":   config.version,
                "stype": config.strategy_type,
                "params": json.dumps(config.parameters),
                "slots": config.slot_count,
                "mode":  config.slot_mode,
                "lot":   LIVE_STARTING_CAPITAL / config.slot_count,   # informational only --
                                                                       # actual sizing always reads
                                                                       # live.blended_capital, not this column
            },
        )
        print(f"  {config.stream_name:<30} {config.version:<6} slots={config.slot_count} mode={config.slot_mode}")

        conn.execute(
            text("""
                INSERT INTO live.blended_capital (model_id, available_capital, updated_at)
                VALUES (:mid, :capital, :now)
            """),
            {"mid": live_model_id, "capital": LIVE_STARTING_CAPITAL, "now": datetime.now(timezone.utc)},
        )
        print(f"  Seeded live.blended_capital: ${LIVE_STARTING_CAPITAL:.2f}")

        print(f"\nModel 3 deployed. Start the executor to begin (dry-run first):")
        print(f"  python -m src.live.blended_executor --dry-run")
        print(f"  python -m src.live.blended_executor")


if __name__ == "__main__":
    deploy()
