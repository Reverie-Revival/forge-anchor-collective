"""
Database operations for the Stream Tester and Model Tester.
"""
import hashlib
import json
import os
import pickle
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from .utils import params_hash

LAST_RUN_PATH       = Path(__file__).parent / ".last_run.pkl"
RUNS_DIR            = Path(__file__).parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)

MODEL_LAST_RUN_PATH = Path(__file__).parent / ".last_model_run.pkl"
MODEL_RUNS_DIR      = Path(__file__).parent / "model_runs"
MODEL_RUNS_DIR.mkdir(exist_ok=True)


def get_engine():
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


# ── Timeframe Presets ────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_timeframe_presets() -> list:
    """Returns active presets as a list of dicts: {preset_id, name, start_date, end_date, description}."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = pd.read_sql(text("""
                SELECT preset_id, name, start_date, end_date, description
                FROM timeframe_presets
                WHERE is_active = TRUE
                ORDER BY start_date ASC
            """), conn)
        return rows.to_dict("records")
    except Exception:
        return []


def save_timeframe_preset(name: str, start_date, end_date, description: str = "") -> int:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO timeframe_presets (name, start_date, end_date, description)
            VALUES (:name, :start_date, :end_date, :description)
            RETURNING preset_id
        """), {"name": name, "start_date": start_date,
               "end_date": end_date or None, "description": description})
        preset_id = row.scalar()
        conn.commit()
    st.cache_data.clear()
    return preset_id


# ── Stream Tests ─────────────────────────────────────────────────────────────

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
    """
    Returns all saved stream tests with a computed timeframe_label column:
    preset name if a preset was used, otherwise a formatted date range.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            return pd.read_sql(text("""
                SELECT
                    st.test_id, st.stream_name, st.stream_version,
                    st.run_number, st.preset_id, st.custom_start, st.custom_end,
                    st.simulation_start, st.simulation_end,
                    st.slot_count, st.slot_mode,
                    st.parameters, st.initial_capital, st.ending_balance,
                    st.total_trades, st.win_rate, st.total_pnl, st.total_return_pct,
                    st.annualized_return_pct, st.avg_winner_pct, st.avg_loser_pct,
                    st.profit_factor, st.max_drawdown_pct, st.avg_hold_candles,
                    st.notes, st.saved_at,
                    COALESCE(
                        tp.name,
                        TO_CHAR(st.custom_start, 'Mon YYYY') || ' → ' ||
                        COALESCE(TO_CHAR(st.custom_end, 'Mon YYYY'), 'present')
                    ) AS timeframe_label
                FROM backtest.stream_tests st
                LEFT JOIN timeframe_presets tp ON st.preset_id = tp.preset_id
                ORDER BY st.run_number ASC, st.saved_at ASC
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

    saved_windows = {(str(r["simulation_start"])[:10], str(r["simulation_end"])[:10]) for r in run_rows}
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
    existing = stream_rows["run_number"].dropna() if (not stream_rows.empty and "run_number" in stream_rows.columns) else pd.Series(dtype=float)
    return int(existing.max()) + 1 if not existing.empty else 1


def save_stream_test(
    stream_name: str,
    params: dict,
    result: dict,
    metrics: dict,
    initial_capital: float,
    ending_balance: float,
    payload: dict,
    preset_id: int = None,
    custom_start=None,
    custom_end=None,
    notes: str = "",
    history: pd.DataFrame = None,
) -> tuple:
    """
    Save a stream test result to the database.
    Exactly one of (preset_id) or (custom_start) must be provided.
    custom_end may be None for open-ended custom windows.
    """
    engine    = get_engine()
    parts     = stream_name.rsplit(" ", 1)
    version   = parts[1] if len(parts) == 2 and parts[1].startswith("v") else "v1"
    stream_nm = parts[0].strip()
    p_hash    = params_hash(params)
    hist      = history if history is not None else pd.DataFrame()
    slot_count = payload.get("slot_count", 1)
    slot_mode  = payload.get("slot_mode", "single")

    run_num = next_run_number(stream_nm, p_hash, hist)

    with engine.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO backtest.stream_tests (
                stream_name, stream_version, run_number,
                preset_id, custom_start, custom_end,
                simulation_start, simulation_end,
                slot_count, slot_mode,
                parameters, initial_capital, ending_balance,
                total_trades, win_rate, total_pnl, total_return_pct,
                annualized_return_pct, avg_winner_pct, avg_loser_pct,
                profit_factor, max_drawdown_pct, avg_hold_candles, notes
            ) VALUES (
                :stream_name, :stream_version, :run_number,
                :preset_id, :custom_start, :custom_end,
                :simulation_start, :simulation_end,
                :slot_count, :slot_mode,
                :parameters, :initial_capital, :ending_balance,
                :total_trades, :win_rate, :total_pnl, :total_return_pct,
                :annualized_return_pct, :avg_winner_pct, :avg_loser_pct,
                :profit_factor, :max_drawdown_pct, :avg_hold_candles, :notes
            ) RETURNING test_id
        """), {
            "stream_name":           stream_nm,
            "stream_version":        version,
            "run_number":            run_num,
            "preset_id":             preset_id,
            "custom_start":          custom_start,
            "custom_end":            custom_end,
            "simulation_start":      result["start"],
            "simulation_end":        result["end"],
            "slot_count":            slot_count,
            "slot_mode":             slot_mode,
            "parameters":            json.dumps(params),
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

    pending = RUNS_DIR / f"pending_{p_hash}_{str(result['start'])[:10]}_{str(result['end'])[:10]}.pkl"
    if pending.exists():
        pending.unlink()

    return test_id, run_num


# ── Model Tester DB ops ──────────────────────────────────────────────────────

def load_model_run_payload(model_test_id: int):
    path = MODEL_RUNS_DIR / f"{model_test_id}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def load_last_model_run():
    if not MODEL_LAST_RUN_PATH.exists():
        return None
    try:
        with open(MODEL_LAST_RUN_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


@st.cache_data(ttl=60)
def load_model_history() -> pd.DataFrame:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            return pd.read_sql(text("""
                SELECT
                    mt.model_test_id, mt.model_id, mt.run_type, mt.run_number,
                    mt.preset_id, mt.custom_start, mt.custom_end,
                    mt.simulation_start, mt.simulation_end,
                    mt.configuration,
                    mt.total_capital, mt.ending_balance, mt.total_trades,
                    mt.win_rate, mt.total_pnl, mt.total_return_pct,
                    mt.annualized_return_pct, mt.max_drawdown_pct,
                    mt.notes, mt.created_at,
                    COALESCE(
                        tp.name,
                        TO_CHAR(mt.custom_start, 'Mon YYYY') || ' → ' ||
                        COALESCE(TO_CHAR(mt.custom_end, 'Mon YYYY'), 'present')
                    ) AS timeframe_label
                FROM backtest.model_tests mt
                LEFT JOIN timeframe_presets tp ON mt.preset_id = tp.preset_id
                ORDER BY mt.run_number ASC, mt.created_at ASC
            """), conn)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_locked_streams_full() -> list:
    """Load locked stream configs with params, lot_size_usd, slot_count, slot_mode."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = pd.read_sql(text("""
                SELECT stream_id, stream_name, stream_version, strategy_type,
                       parameters, lot_size_usd, slot_count, slot_mode,
                       grade, description, notes
                FROM backtest.streams
                ORDER BY stream_id
            """), conn)
        result = []
        for _, row in rows.iterrows():
            params = row["parameters"] if isinstance(row["parameters"], dict) \
                     else json.loads(row["parameters"])
            result.append({
                "stream_id":      int(row["stream_id"]),
                "stream_name":    row["stream_name"],
                "stream_version": row["stream_version"],
                "full_name":      f"{row['stream_name']} {row['stream_version']}",
                "strategy_type":  row["strategy_type"],
                "params":         params,
                "lot_size_usd":   float(row["lot_size_usd"]),
                "slot_count":     int(row["slot_count"]),
                "slot_mode":      str(row["slot_mode"]),
                "grade":          row["grade"],
                "description":    row["description"],
                "notes":          row["notes"],
            })
        return result
    except Exception:
        return []


def _alloc_hash(allocations: dict) -> str:
    return hashlib.md5(
        json.dumps(allocations, sort_keys=True).encode()
    ).hexdigest()[:10]


def load_pending_model_runs(run_rows: list) -> list:
    if not run_rows:
        return []
    try:
        cfg = run_rows[0]["configuration"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        alloc = cfg.get("allocations", {})
        ah = _alloc_hash(alloc)
    except Exception:
        return []
    saved_windows = {(str(r["simulation_start"])[:10], str(r["simulation_end"])[:10]) for r in run_rows}
    return _pending_model_for_hash(ah, exclude=saved_windows)


def _pending_model_for_hash(ah: str, exclude: set = None) -> list:
    exclude = exclude or set()
    pending = []
    for f in sorted(MODEL_RUNS_DIR.glob(f"pending_{ah}_*.pkl"), key=lambda x: x.stat().st_mtime):
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


def next_model_run_number(model_id: int, alloc_h: str) -> int:
    """Query DB fresh — never rely on a passed DataFrame to avoid stale-history bugs."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT run_number, configuration
            FROM backtest.model_tests
            WHERE model_id = :m
            ORDER BY run_number ASC
        """), {"m": model_id}).fetchall()
    if not rows:
        return 1
    for run_num, cfg in rows:
        try:
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            if _alloc_hash(cfg.get("allocations", {})) == alloc_h:
                return int(run_num)
        except Exception:
            pass
    return max(int(r[0]) for r in rows) + 1


def save_model_test(
    payload: dict,
    preset_id: int = None,
    custom_start=None,
    custom_end=None,
    notes: str = "",
    history: pd.DataFrame = None,  # kept for backwards-compat, ignored
) -> tuple:
    """
    Save a model test result to the database.
    Exactly one of (preset_id) or (custom_start) must be provided.
    """
    engine   = get_engine()
    cm       = payload["combined_metrics"]
    alloc    = payload["allocations"]
    ah       = _alloc_hash(alloc)
    model_id = payload.get("model_id", 1)

    run_num = next_model_run_number(model_id, ah)
    configuration = json.dumps({"allocations": alloc})

    with engine.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO backtest.model_tests (
                model_id, run_type, run_number,
                preset_id, custom_start, custom_end,
                simulation_start, simulation_end,
                status, configuration,
                total_capital, ending_balance,
                total_trades, win_rate, total_pnl,
                total_return_pct, annualized_return_pct, max_drawdown_pct, notes
            ) VALUES (
                :model_id, 'historical', :run_number,
                :preset_id, :custom_start, :custom_end,
                :simulation_start, :simulation_end,
                'completed', :configuration,
                :total_capital, :ending_balance,
                :total_trades, :win_rate, :total_pnl,
                :total_return_pct, :annualized_return_pct, :max_drawdown_pct, :notes
            ) RETURNING model_test_id
        """), {
            "model_id":             model_id,
            "run_number":           run_num,
            "preset_id":            preset_id,
            "custom_start":         custom_start,
            "custom_end":           custom_end,
            "simulation_start":     payload["start"],
            "simulation_end":       payload["end"],
            "configuration":        configuration,
            "total_capital":        payload["total_capital"],
            "ending_balance":       payload["total_capital"] + (cm["total_pnl"] or 0),
            "total_trades":         cm["total_trades"],
            "win_rate":             cm["win_rate"],
            "total_pnl":            cm["total_pnl"],
            "total_return_pct":     cm["total_return_pct"],
            "annualized_return_pct": cm["annualized_return_pct"],
            "max_drawdown_pct":     cm["max_drawdown_pct"],
            "notes":                notes,
        })
        model_test_id = row.scalar()
        conn.commit()

    pkl_path = MODEL_RUNS_DIR / f"{model_test_id}.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(payload, f)

    pending = MODEL_RUNS_DIR / f"pending_{ah}_{str(payload['start'])[:10]}_{str(payload['end'])[:10]}.pkl"
    if pending.exists():
        pending.unlink()

    return model_test_id, run_num
