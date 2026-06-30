"""
Programmatic entry point for model-level backtesting.
Run from Claude Code — results are written to model_runs/ for Model Tester to display.

Usage:
    from src.backtester.model_runner import run_model

    # Equal allocation across locked streams
    result = run_model(start="2019-01-01", end="2023-12-31")

    # Custom allocation
    result = run_model(
        allocations={
            "Momentum Rider v1": {"lot_size_usd": 20.0, "slot_count": 2, "slot_mode": "scale_up"},
            "Dip Hunter v1":     {"lot_size_usd": 10.0, "slot_count": 2, "slot_mode": "scale_down"},
            "Breakout Scout v1": {"lot_size_usd": 20.0, "slot_count": 1, "slot_mode": "single"},
        },
        start="2019-01-01",
        end="2023-12-31",
    )
"""
import hashlib
import json
import os
import pickle
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from .model_engine import run_model_backtest

load_dotenv()

MODEL_LAST_RUN_PATH = Path(__file__).parent.parent / "app" / ".last_model_run.pkl"
MODEL_RUNS_DIR      = Path(__file__).parent.parent / "app" / "model_runs"


def _get_engine():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "forge_anchor")
        user = os.getenv("DB_USER", "")
        pwd  = os.getenv("DB_PASSWORD", "")
        auth = f"{user}:{pwd}@" if user else ""
        db_url = f"postgresql+psycopg2://{auth}{host}:{port}/{name}"
    elif db_url.startswith("postgresql://") and "+psycopg2" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(db_url)


def _alloc_hash(allocations: dict) -> str:
    return hashlib.md5(
        json.dumps(allocations, sort_keys=True).encode()
    ).hexdigest()[:10]


def _load_locked_streams(model_id: int = 1) -> list:
    engine = _get_engine()
    with engine.connect() as conn:
        rows = pd.read_sql(text("""
            SELECT stream_id, stream_name, stream_version, parameters,
                   lot_size_usd, slot_count, slot_mode
            FROM backtest.streams
            WHERE model_id = :model_id
            ORDER BY stream_id
        """), conn, params={"model_id": model_id})
    configs = []
    for _, row in rows.iterrows():
        params = row["parameters"] if isinstance(row["parameters"], dict) \
                 else json.loads(row["parameters"])
        configs.append({
            "stream_id":    int(row["stream_id"]),
            "stream_name":  f"{row['stream_name']} {row['stream_version']}",
            "params":       params,
            "lot_size_usd": float(row["lot_size_usd"]),
            "slot_count":   int(row["slot_count"]),
            "slot_mode":    str(row["slot_mode"]),
        })
    return configs


def run_model(
    allocations: dict = None,
    start: str = None,
    end: str = None,
    model_id: int = 1,
) -> dict:
    """
    Run a model-level backtest. Loads locked streams from DB and applies allocations.

    allocations: {stream_full_name: {lot_size_usd, slot_count, slot_mode}}
        Keys are stream full names (e.g. "Momentum Rider v1").
        Any key missing from the dict uses the value locked in backtest.streams.
        slot_mode can also be overridden per-stream here for experimentation.
    """
    stream_configs = _load_locked_streams(model_id)

    if allocations:
        for sc in stream_configs:
            if sc["stream_name"] in allocations:
                alloc = allocations[sc["stream_name"]]
                sc["lot_size_usd"] = float(alloc.get("lot_size_usd", sc["lot_size_usd"]))
                sc["slot_count"]   = int(alloc.get("slot_count",     sc["slot_count"]))
                sc["slot_mode"]    = str(alloc.get("slot_mode",      sc["slot_mode"]))

    effective_alloc = {
        sc["stream_name"]: {
            "lot_size_usd": sc["lot_size_usd"],
            "slot_count":   sc["slot_count"],
            "slot_mode":    sc["slot_mode"],
        }
        for sc in stream_configs
    }

    payload = run_model_backtest(stream_configs, start=start, end=end)
    payload["model_id"]    = model_id
    payload["allocations"] = effective_alloc

    MODEL_RUNS_DIR.mkdir(exist_ok=True)
    alloc_h   = _alloc_hash(effective_alloc)
    start_str = str(payload["start"])[:10]
    end_str   = str(payload["end"])[:10]

    with open(MODEL_LAST_RUN_PATH, "wb") as f:
        pickle.dump(payload, f)

    pending = MODEL_RUNS_DIR / f"pending_{alloc_h}_{start_str}_{end_str}.pkl"
    with open(pending, "wb") as f:
        pickle.dump(payload, f)

    cm  = payload["combined_metrics"]
    bh  = payload["bh"]
    ann = cm["annualized_return_pct"]

    return {
        "period":            f"{start_str} → {end_str}",
        "total_capital":     f"${payload['total_capital']:.2f}",
        "combined_trades":   cm["total_trades"],
        "annualized_return": f"{ann:+.1f}%" if ann is not None else "—",
        "win_rate":          f"{cm['win_rate']*100:.1f}%" if cm["win_rate"] else "—",
        "max_drawdown":      f"{cm['max_drawdown_pct']:.1f}%" if cm["max_drawdown_pct"] is not None else "—",
        "btc_bh_annualized": f"{bh['annualized_return_pct']:+.1f}%" if bh.get("annualized_return_pct") else "—",
        "streams": {
            sr["stream_name"]: {
                "allocation":  f"${sr['lot_size_usd']:.0f}/lot × {sr['slot_count']} slots ({sr['slot_mode']}) = ${sr['initial_capital']:.0f}",
                "trades":      sr["metrics"]["total_trades"],
                "ending":      f"${sr['ending_balance']:.2f}",
                "annualized":  f"{sr['metrics']['annualized_return_pct']:+.1f}%" if sr["metrics"]["annualized_return_pct"] is not None else "—",
                "win_rate":    f"{sr['metrics']['win_rate']*100:.1f}%" if sr["metrics"]["win_rate"] else "—",
            }
            for sr in payload["stream_results"]
        },
    }
