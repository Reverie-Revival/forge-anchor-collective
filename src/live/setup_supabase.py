"""
Supabase live schema setup + Model 1 seed.

Idempotent — safe to run multiple times. Run this before starting the executor
on any new Supabase project, or after adding new schema columns.

What it does:
  1. Applies any missing columns to live.streams and live.lots
  2. Ensures live.executor_state row exists
  3. Seeds live.models + live.streams for Model 1 if not already present
  4. Seeds the open Breakout Scout lot (July 3 fill) if no open lots exist

Usage:
    python -m src.live.setup_supabase
"""
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def _get_engine():
    url = os.getenv("SUPABASE_DATABASE_URL", "")
    if not url:
        print("ERROR: SUPABASE_DATABASE_URL not set — must point to Supabase, not local postgres")
        sys.exit(1)
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


# Model 1 stream definitions — source of truth for live deployment
# Parameters pulled from backtest.stream_configs at build time and hardcoded here
# so setup_supabase.py has no dependency on local postgres
MODEL1_STREAMS = [
    {
        "stream_name": "Momentum Rider",
        "stream_version": "v2",
        "strategy_type": "ema_crossover",
        "slot_count": 1,
        "slot_mode": "single",
        "lot_size_usd": 33.33,
        "parameters": {
            "core_signal": "ema_crossover",
            "primary_timeframe": "4h",
            "core_params": {"ema_short": 30, "ema_long": 120},
            "filters": {
                "rsi": {"min": 55, "max": None, "period": 14},
                "trend_context": {"require": "above", "sma_period": 200},
            },
            "sentiment": {"fear_greed": {"min": 25, "max": None}},
            "position": {
                "entry_order_type": "limit",
                "trailing_stop_pct": 7.0,
                "min_hold_candles": 12,
                "entry_expiry_candles": 2,
            },
        },
    },
    {
        "stream_name": "Dip Hunter",
        "stream_version": "v2",
        "strategy_type": "rsi_recovery",
        "slot_count": 1,
        "slot_mode": "single",
        "lot_size_usd": 33.33,
        "parameters": {
            "core_signal": "rsi_recovery",
            "primary_timeframe": "1h",
            "core_params": {
                "rsi_period": 14,
                "rsi_threshold": 30,
                "require_bullish_candle": True,
            },
            "filters": {
                "rsi": {"min": 35},
                "drawdown_from_high": {"min_drop_pct": 25.0, "lookback_days": 90},
            },
            "sentiment": {"fear_greed": {"max": 20}},
            "position": {
                "entry_order_type": "limit",
                "trailing_stop_pct": 10.0,
                "min_hold_candles": 48,
                "max_hold_candles": 240,
                "entry_expiry_candles": 1,
            },
        },
    },
    {
        "stream_name": "Breakout Scout",
        "stream_version": "v2",
        "strategy_type": "range_breakout",
        "slot_count": 1,
        "slot_mode": "single",
        "lot_size_usd": 33.33,
        "parameters": {
            "core_signal": "range_breakout",
            "primary_timeframe": "1h",
            "core_params": {"breakout_lookback": 24},
            "filters": {
                "trend_context": {"require": "above", "sma_period": 200},
                "bollinger": {
                    "period": 20,
                    "std_dev": 2.0,
                    "squeeze": {"max_bandwidth_pct": 6.0},
                },
                "atr_regime": {"period": 14, "avg_period": 30, "max_pct_of_avg": 90},
                "breakout_candle": {"body_ratio_min": 0.4, "close_position_min": 0.6},
            },
            "sentiment": {"fear_greed": {"min": 55}},
            "position": {
                "entry_order_type": "limit",
                "trailing_stop_pct": 10.0,
                "entry_expiry_candles": 2,
            },
        },
    },
]

# Open Breakout Scout lot — filled July 3 2026, never closed
# Fill details pulled from Kraken TradesHistory on 2026-07-21
OPEN_BS_LOT = {
    "entry_price":     62710.10,
    "btc_quantity":    0.00053149,
    "opening_capital": 33.33,
    "entry_order_id":  "OF6YSN-4RZAY-LLJMSQ",
    "high_water_mark": 66894.60,   # highest 1h close since fill as of 2026-07-21
    "entry_reason":    "signal:range_breakout",
    "opened_at":       datetime(2026, 7, 3, 21, 0, 54, tzinfo=timezone.utc),
}


def apply_schema_migrations(conn) -> None:
    print("Applying schema migrations...")
    conn.execute(text("""
        ALTER TABLE live.streams
            ADD COLUMN IF NOT EXISTS slot_mode   TEXT         NOT NULL DEFAULT 'single',
            ADD COLUMN IF NOT EXISTS lot_size_usd NUMERIC(10,2) NOT NULL DEFAULT 33.33
    """))
    conn.execute(text("""
        ALTER TABLE live.lots
            ADD COLUMN IF NOT EXISTS entry_expiry_at TIMESTAMPTZ
    """))
    print("  Schema migrations applied.")


def ensure_executor_state(conn) -> None:
    conn.execute(text("""
        INSERT INTO live.executor_state (id, last_run_at)
        VALUES (1, NOW())
        ON CONFLICT (id) DO NOTHING
    """))
    print("  executor_state row ensured.")


def seed_model1(conn) -> int:
    existing = conn.execute(
        text("SELECT model_id FROM live.models WHERE model_version = 1")
    ).fetchone()
    if existing:
        print(f"  Model 1 already in live.models (model_id={existing[0]}) — skipping.")
        return existing[0]

    row = conn.execute(text("""
        INSERT INTO live.models
            (model_version, description, deployed_at, based_on_run_id, status)
        VALUES (1, 'Model 1 — MR v2 + DH v2 + BS v2 — $33.33/stream', :now, 15, 'active')
        RETURNING model_id
    """), {"now": datetime.now(timezone.utc)})
    model_id = row.scalar()
    print(f"  Created live.models row: model_id={model_id}")
    return model_id


def seed_streams(conn, model_id: int) -> dict:
    existing = conn.execute(
        text("SELECT stream_name, stream_id FROM live.streams WHERE model_id = :mid"),
        {"mid": model_id},
    ).fetchall()
    if existing:
        print(f"  live.streams already seeded ({len(existing)} rows) — skipping.")
        return {r.stream_name: r.stream_id for r in existing}

    stream_ids = {}
    for s in MODEL1_STREAMS:
        row = conn.execute(text("""
            INSERT INTO live.streams
                (model_id, stream_name, stream_version, strategy_type,
                 parameters, slot_count, slot_mode, lot_size_usd)
            VALUES
                (:mid, :name, :ver, :stype,
                 :params::jsonb, :slots, :mode, :lot)
            RETURNING stream_id
        """), {
            "mid":    model_id,
            "name":   s["stream_name"],
            "ver":    s["stream_version"],
            "stype":  s["strategy_type"],
            "params": json.dumps(s["parameters"]),
            "slots":  s["slot_count"],
            "mode":   s["slot_mode"],
            "lot":    s["lot_size_usd"],
        })
        sid = row.scalar()
        stream_ids[s["stream_name"]] = sid
        print(f"  Seeded stream: {s['stream_name']} {s['stream_version']} (stream_id={sid})")
    return stream_ids


def seed_open_bs_lot(conn, model_id: int, stream_ids: dict) -> None:
    open_count = conn.execute(
        text("SELECT COUNT(*) FROM live.lots WHERE status IN ('OPEN', 'PENDING')")
    ).scalar()
    if open_count > 0:
        print(f"  live.lots already has {open_count} open/pending row(s) — skipping lot seed.")
        return

    bs_stream_id = stream_ids.get("Breakout Scout")
    if not bs_stream_id:
        print("  ERROR: Breakout Scout stream_id not found — cannot seed lot.")
        return

    lot = OPEN_BS_LOT
    conn.execute(text("""
        INSERT INTO live.lots
            (model_id, stream_id, slot_number, lot_sequence, status,
             opening_capital, btc_quantity, entry_price, entry_order_id,
             high_water_mark, entry_expiry_at, entry_reason, opened_at)
        VALUES
            (:mid, :sid, 1, 1, 'OPEN',
             :capital, :qty, :price, :order_id,
             :hwm, NULL, :reason, :opened_at)
    """), {
        "mid":       model_id,
        "sid":       bs_stream_id,
        "capital":   lot["opening_capital"],
        "qty":       lot["btc_quantity"],
        "price":     lot["entry_price"],
        "order_id":  lot["entry_order_id"],
        "hwm":       lot["high_water_mark"],
        "reason":    lot["entry_reason"],
        "opened_at": lot["opened_at"],
    })
    print(f"  Seeded open Breakout Scout lot: {lot['btc_quantity']} BTC @ ${lot['entry_price']:,.2f}  HWM=${lot['high_water_mark']:,.2f}  trail stop=${lot['high_water_mark'] * 0.90:,.2f}")


def run() -> None:
    print("=== Forge Anchor — Supabase Setup ===\n")
    engine = _get_engine()

    with engine.begin() as conn:
        print("1. Schema migrations")
        apply_schema_migrations(conn)

        print("\n2. Executor state")
        ensure_executor_state(conn)

        print("\n3. Model 1 seed")
        model_id = seed_model1(conn)

        print("\n4. Stream seed")
        stream_ids = seed_streams(conn, model_id)

        print("\n5. Open lot repair (Breakout Scout July 3 fill)")
        seed_open_bs_lot(conn, model_id, stream_ids)

    print("\n=== Setup complete. Verify with: ===")
    print("  SELECT * FROM live.models;")
    print("  SELECT stream_id, stream_name, lot_size_usd FROM live.streams;")
    print("  SELECT lot_id, stream_id, status, entry_price, high_water_mark FROM live.lots;")


if __name__ == "__main__":
    run()
