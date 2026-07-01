"""
Layer 1 — Signal parity tests.

Runs all three locked Model 1 stream configs through the backtester signal engine
against the Primary v2 window (2022-01-01 → 2026-06-28) and asserts exact trade
counts match the locked backtest results.

Configs and expected counts are read directly from the DB (backtest.streams +
backtest.stream_tests) — never hardcoded here. This ensures the test always
reflects exactly what was locked, not a transcription of it.

This test MUST pass before any commit to live-model-1. If it fails, something in
the signal/indicator logic changed and the live engine is no longer running what
was validated.

Run:
    pytest tests/live/test_signal_parity.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.backtester.engine import run_backtest

load_dotenv()

BACKTEST_MODEL_ID = 1


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _load_streams_from_db():
    """
    Pull locked stream configs and their Primary v2 trade counts from the DB.
    Returns list of dicts: {name, params, expected_trades, sim_start, sim_end}
    """
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    s.stream_name,
                    s.parameters,
                    s.slot_count,
                    s.slot_mode,
                    st.total_trades,
                    st.simulation_start,
                    st.simulation_end
                FROM backtest.streams s
                JOIN backtest.stream_tests st ON s.locked_test_id = st.test_id
                WHERE s.model_id = :mid
                ORDER BY s.stream_id
            """),
            {"mid": BACKTEST_MODEL_ID},
        ).fetchall()
    return [
        {
            "name": r.stream_name,
            "params": r.parameters,
            "slot_count": r.slot_count,
            "slot_mode": r.slot_mode,
            "expected_trades": r.total_trades,
            "sim_start": r.simulation_start.strftime("%Y-%m-%d"),
            "sim_end": r.simulation_end.strftime("%Y-%m-%d"),
        }
        for r in rows
    ]


# Load once at collection time — this is the source of truth
_STREAMS = _load_streams_from_db()


@pytest.mark.parametrize("stream", _STREAMS, ids=[s["name"] for s in _STREAMS])
def test_trade_count_matches_locked_backtest(stream):
    """
    Trade count must exactly match what was locked in backtest.stream_tests.
    Any deviation means the signal engine has diverged from validated behavior.
    Do NOT deploy to live-model-1 until this passes.
    """
    result = run_backtest(
        params=stream["params"],
        start=stream["sim_start"],
        end=stream["sim_end"],
        slot_count=stream["slot_count"],
        slot_mode=stream["slot_mode"],
        stream_name=stream["name"],
        lot_size_usd=10.0,
    )
    trades = result["trades"]
    actual = len(trades) if not trades.empty else 0
    expected = stream["expected_trades"]

    assert actual == expected, (
        f"{stream['name']}: expected {expected} trades on locked window "
        f"({stream['sim_start']} → {stream['sim_end']}), got {actual}. "
        f"Signal engine has diverged from locked backtest — do NOT deploy to live-model-1."
    )
