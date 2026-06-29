"""
Database operations for the Stream Tester.
"""
import json
import os
import pickle
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from .utils import params_hash, label_window

LAST_RUN_PATH = Path(__file__).parent / ".last_run.pkl"
RUNS_DIR      = Path(__file__).parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)


def get_engine():
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if db_url.startswith("postgresql://") and "+psycopg2" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(db_url)


def load_run_payload(test_id: int):
    path = RUNS_DIR / f"{test_id}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


@st.cache_data(ttl=60)
def load_stream_history() -> pd.DataFrame:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            return pd.read_sql(text("""
                SELECT test_id, stream_name, stream_version,
                       run_number, window_name, parameters,
                       test_start, test_end, total_trades, profit_factor,
                       annualized_return_pct, max_drawdown_pct,
                       avg_winner_pct, avg_loser_pct, win_rate,
                       total_return_pct, ending_balance, initial_capital,
                       saved_at, notes
                FROM backtest.stream_tests
                ORDER BY run_number ASC, saved_at ASC
            """), conn)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_locked_streams() -> dict:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = pd.read_sql(text("""
                SELECT stream_name, stream_version, description, grade, notes
                FROM backtest.streams
            """), conn)
        return {
            f"{r['stream_name']} {r['stream_version']}": {
                "description": r["description"],
                "grade":       r["grade"],
                "notes":       r["notes"],
            }
            for _, r in rows.iterrows()
        }
    except Exception:
        return {}


def load_pending_runs(run_rows: list) -> list:
    if not run_rows:
        return []
    try:
        p  = run_rows[0]["parameters"] if isinstance(run_rows[0]["parameters"], dict) \
             else json.loads(run_rows[0]["parameters"])
        ph = params_hash(p)
    except Exception:
        return []

    saved_windows = {(str(r["test_start"])[:10], str(r["test_end"])[:10]) for r in run_rows}
    return _pending_for_hash(ph, exclude=saved_windows)


def _pending_for_hash(ph: str, exclude: set = None) -> list:
    exclude = exclude or set()
    pending = []
    for f in sorted(RUNS_DIR.glob(f"pending_{ph}_*.pkl"), key=lambda x: x.stat().st_mtime):
        parts = f.stem.split("_")
        if len(parts) >= 4:
            start_str, end_str = parts[2], parts[3]
            if (start_str, end_str) not in exclude:
                try:
                    with open(f, "rb") as fh:
                        payload = pickle.load(fh)
                    pending.append({"file": f, "payload": payload,
                                    "start": start_str, "end": end_str})
                except Exception:
                    pass
    return pending


def load_latest_run(selected_stream: str):
    if not LAST_RUN_PATH.exists():
        return None
    try:
        with open(LAST_RUN_PATH, "rb") as f:
            payload = pickle.load(f)
        if payload.get("stream_name", "").strip() == selected_stream.strip():
            return payload
    except Exception:
        pass
    return None


def next_run_number(stream_nm: str, params_h: str, history: pd.DataFrame) -> int:
    stream_rows = history[history["stream_name"] == stream_nm] if not history.empty else pd.DataFrame()
    if not stream_rows.empty:
        for _, row in stream_rows.iterrows():
            try:
                p = row["parameters"] if isinstance(row["parameters"], dict) \
                    else json.loads(row["parameters"])
                if params_hash(p) == params_h:
                    return int(row["run_number"])
            except Exception:
                pass
    existing = stream_rows["run_number"].dropna()
    return int(existing.max()) + 1 if not existing.empty else 1


def save_stream_test(stream_name, params, result, metrics, initial_capital, ending_balance,
                     payload: dict, window_name: str = "", notes: str = "",
                     history: pd.DataFrame = None) -> tuple:
    engine    = get_engine()
    parts     = stream_name.rsplit(" ", 1)
    version   = parts[1] if len(parts) == 2 and parts[1].startswith("v") else "v1"
    stream_nm = parts[0].strip()
    p_hash    = params_hash(params)
    hist      = history if history is not None else pd.DataFrame()

    run_num = next_run_number(stream_nm, p_hash, hist)
    win_nm  = window_name or label_window(result["start"], result["end"])

    with engine.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO backtest.stream_tests (
                stream_name, stream_version, run_number, window_name, parameters,
                test_start, test_end, n_slots, initial_capital, ending_balance,
                total_trades, win_rate, total_pnl, total_return_pct,
                annualized_return_pct, avg_winner_pct, avg_loser_pct,
                profit_factor, max_drawdown_pct, avg_hold_candles, notes
            ) VALUES (
                :stream_name, :stream_version, :run_number, :window_name, :parameters,
                :test_start, :test_end, :n_slots, :initial_capital, :ending_balance,
                :total_trades, :win_rate, :total_pnl, :total_return_pct,
                :annualized_return_pct, :avg_winner_pct, :avg_loser_pct,
                :profit_factor, :max_drawdown_pct, :avg_hold_candles, :notes
            ) RETURNING test_id
        """), {
            "stream_name":           stream_nm,
            "stream_version":        version,
            "run_number":            run_num,
            "window_name":           win_nm,
            "parameters":            json.dumps(params),
            "test_start":            result["start"],
            "test_end":              result["end"],
            "n_slots":               result.get("n_slots", 2),
            "initial_capital":       initial_capital,
            "ending_balance":        ending_balance,
            "total_trades":          metrics["total_trades"],
            "win_rate":              metrics["win_rate"],
            "total_pnl":             metrics["total_pnl"],
            "total_return_pct":      metrics["total_return_pct"],
            "annualized_return_pct": metrics["annualized_return_pct"],
            "avg_winner_pct":        metrics["avg_winner_pct"],
            "avg_loser_pct":         metrics["avg_loser_pct"],
            "profit_factor":         metrics["profit_factor"],
            "max_drawdown_pct":      metrics["max_drawdown_pct"],
            "avg_hold_candles":      metrics["avg_hold_candles"],
            "notes":                 notes,
        })
        test_id = row.scalar()
        conn.commit()

    pkl_path = RUNS_DIR / f"{test_id}.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(payload, f)

    # Remove the matching pending file now that it's saved
    pending = RUNS_DIR / f"pending_{p_hash}_{str(result['start'])[:10]}_{str(result['end'])[:10]}.pkl"
    if pending.exists():
        pending.unlink()

    return test_id, run_num, win_nm
