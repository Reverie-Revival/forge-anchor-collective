"""
One-time deployment script for Model 2 (VR v1 + DH v3 + BS v3 + MR v4).
Run once on Supabase before starting the executor.

Usage:
    python -m src.live.deploy_model2

What it does:
  1. Creates a live.models row (model_version=2, status=active)
  2. Copies the 4 locked stream configs from backtest.model_streams (joined
     through backtest.stream_configs -> backtest.streams -- the current v3
     schema; NOT backtest.streams directly, which is identity-only and has
     no model_id/parameters/slot_count columns anymore) into live.streams
     at $25/lot each ($100 total)
  3. Seeds live.capital_reserve (docs/decisions/008 pooled solvency reserve)
     at $100 baseline/pool, hard_floor = 10.0 / max(stream weight) = $40 for
     four equal-weighted $25 streams
  4. Seeds live.btc_bucket (docs/decisions/008 profit-skim bucket) at zero --
     starts empty, gets funded by order_manager._update_reserve's skims off
     real realized gains once the pool is above baseline
  5. Prints a confirmation table

Safe to inspect -- will abort if Model 2 is already deployed.
"""
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

LIVE_MODEL_VERSION = 2
BASED_ON_MODEL_TEST_ID = 156  # Primary v2, live-replay validated, +15.90% ann / 106 trades
LIVE_STARTING_CAPITAL = 100.00
DESCRIPTION = "Model 2 — VR v1 + DH v3 + BS v3 + MR v4 — equal $25/stream"


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
    # backtest.* only exists on local Postgres -- Supabase has no backtest
    # schema at all (live schema + market_data + sentiment_data only).
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def deploy():
    local_engine = _get_local_engine()
    with local_engine.connect() as lconn:
        configs = lconn.execute(
            text("""
                SELECT ms.stream_config_id, ms.lot_size_usd, sc.version, sc.parameters,
                       sc.slot_count, sc.slot_mode, s.stream_name, s.strategy_type
                FROM backtest.model_streams ms
                JOIN backtest.stream_configs sc ON sc.stream_config_id = ms.stream_config_id
                JOIN backtest.streams s ON s.stream_id = sc.stream_id
                WHERE ms.model_id = 2
                ORDER BY ms.id
            """)
        ).fetchall()
        if not configs:
            raise RuntimeError("No backtest.model_streams rows for model_id=2 -- finalize the model first")

    baseline_total = sum(float(c.lot_size_usd) for c in configs)
    max_weight = max(float(c.lot_size_usd) for c in configs) / baseline_total
    hard_floor = 10.0 / max_weight

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

        for c in configs:
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
                    "mid":    live_model_id,
                    "name":   c.stream_name,
                    "ver":    c.version,
                    "stype":  c.strategy_type,
                    "params": json.dumps(c.parameters),
                    "slots":  c.slot_count,
                    "mode":   c.slot_mode,
                    "lot":    float(c.lot_size_usd),
                },
            )
            print(f"  {c.stream_name:<20} {c.version:<6} slots={c.slot_count} mode={c.slot_mode} lot=${float(c.lot_size_usd):.2f}")

        conn.execute(
            text("""
                INSERT INTO live.capital_reserve
                    (model_id, baseline_total, pool_balance, hard_floor, updated_at)
                VALUES (:mid, :baseline, :baseline, :floor, :now)
            """),
            {"mid": live_model_id, "baseline": baseline_total, "floor": hard_floor,
             "now": datetime.now(timezone.utc)},
        )
        print(f"  Seeded live.capital_reserve: baseline=pool=${baseline_total:.2f} hard_floor=${hard_floor:.2f}")

        conn.execute(
            text("""
                INSERT INTO live.btc_bucket (model_id, updated_at)
                VALUES (:mid, :now)
            """),
            {"mid": live_model_id, "now": datetime.now(timezone.utc)},
        )
        print("  Seeded live.btc_bucket: empty (funded by future skims)")

        print(f"\nModel 2 deployed. Start the executor to begin (dry-run first):")
        print(f"  LIVE_MODEL_VERSION=2 python -m src.live.executor --dry-run")
        print(f"  LIVE_MODEL_VERSION=2 python -m src.live.executor")


if __name__ == "__main__":
    deploy()
