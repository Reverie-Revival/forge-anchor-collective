"""
Layer 3 — Isolation test: Model 3's code must never touch Model 1's data,
even though both share the same live schema (and, in production, the same
Kraken account). This is the test that matters most given both models trade
real money out of one account.

Seeds a fake Model 1 lot + executor_state row, runs a full Model 3 cycle
through it, and asserts Model 1's rows are byte-for-byte unchanged.

Run:
    pytest tests/live/test_blended_isolation.py -v -s
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import blended_order_manager as order_manager
from tests.live._fake_kraken import FakeKraken
from src.live import blended_position_monitor as position_monitor
from src.live.blended_executor import _write_last_run, _read_last_run
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 998   # "Model 1 stand-in" for this test
TEST_M3_MODEL_VERSION = 997

PARAMS = {
    "slots": {"slot_capital_weight": [20, 20, 20, 20, 20]},
    "filters": {}, "sentiment": False,
    "position": {"compound": True, "trailing_stop_pct": 5.0, "trail_arm_gain_pct": 4,
                "cumulative_drop_pcts": [1, 2, 5, 10], "entry_expiry_candles": 2,
                "capitulation_stop_pct": 15},
    "core_params": {"dip_pct": 1.0}, "core_signal": "fear_dip", "primary_timeframe": "4h",
}


@pytest.fixture
def dual_model_sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version IN (:v1, :v2)"),
                     {"v1": TEST_MODEL_VERSION, "v2": TEST_M3_MODEL_VERSION})

        m1_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- Model 1 stand-in', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        m1_stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'TEST M1 Stream', 'v1', 'momentum', CAST(:p AS jsonb), 1, 'single', 33.33)
            RETURNING stream_id
        """), {"mid": m1_id, "p": json.dumps({"primary_timeframe": "4h"})}).scalar()
        m1_lot_id = conn.execute(text("""
            INSERT INTO live.lots (model_id, stream_id, slot_number, lot_sequence, status,
                                   opening_capital, btc_quantity, entry_price)
            VALUES (:mid, :sid, 1, 1, 'OPEN', 33.33, 0.00066, 50000.00)
            RETURNING lot_id
        """), {"mid": m1_id, "sid": m1_stream_id}).scalar()
        conn.execute(text("""
            INSERT INTO live.executor_state (id, last_run_at, model_id) VALUES (:id, now(), :mid)
        """), {"id": m1_id, "mid": m1_id})

        m3_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- Model 3 stand-in', 0, 'active') RETURNING model_id
        """), {"v": TEST_M3_MODEL_VERSION}).scalar()
        m3_stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'TEST Grid Stacker Blended', 'v8', 'blended_dca', CAST(:p AS jsonb), 5, 'blended', 20.0)
            RETURNING stream_id
        """), {"mid": m3_id, "p": json.dumps(PARAMS)}).scalar()
        conn.execute(text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, 100.00)"),
                     {"mid": m3_id})

    m3_stream = {
        "stream_id": m3_stream_id, "model_id": m3_id, "stream_name": "TEST Grid Stacker Blended",
        "parameters": PARAMS, "slot_count": 5, "slot_mode": "blended", "lot_size_usd": 20.0,
    }

    yield engine, m1_id, m1_lot_id, m3_stream, m3_id

    with engine.begin() as conn:
        for mid in (m1_id, m3_id):
            conn.execute(text("""
                DELETE FROM live.blended_fills WHERE position_id IN
                (SELECT position_id FROM live.blended_positions WHERE model_id = :mid)
            """), {"mid": mid})
            conn.execute(text("DELETE FROM live.blended_positions WHERE model_id = :mid"), {"mid": mid})
            conn.execute(text("DELETE FROM live.blended_capital WHERE model_id = :mid"), {"mid": mid})
            conn.execute(text("DELETE FROM live.lots WHERE model_id = :mid"), {"mid": mid})
            conn.execute(text("DELETE FROM live.executor_state WHERE model_id = :mid"), {"mid": mid})
            conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": mid})
            conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": mid})


def test_blended_flow_never_touches_model1_lots_or_heartbeat(dual_model_sandbox):
    engine, m1_id, m1_lot_id, m3_stream, m3_id = dual_model_sandbox
    kraken = FakeKraken()

    with engine.connect() as conn:
        m1_lot_snapshot_before = dict(conn.execute(
            text("SELECT * FROM live.lots WHERE lot_id = :lid"), {"lid": m1_lot_id}
        ).mappings().fetchone())
        m1_heartbeat_before = conn.execute(
            text("SELECT last_run_at FROM live.executor_state WHERE model_id = :mid"), {"mid": m1_id}
        ).scalar()

    # --- run a full Model 3 cycle: entry -> fill -> add -> fill -> exit ---
    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, m3_stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_entry(conn, kraken, {m3_stream["stream_id"]: m3_stream}, dry_run=False)

    kraken._next_price = 49000.0
    with engine.begin() as conn:
        order_manager.check_cascade_add_trigger(conn, m3_stream, latest_close=49000.0, kraken=kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending_add(conn, kraken, {m3_stream["stream_id"]: m3_stream}, dry_run=False)

    with engine.begin() as conn:
        candle_row = {m3_stream["stream_id"]: {"close": 60000.0, "low": 57000.0}}
        stops = position_monitor.check_all(
            conn, {m3_stream["stream_id"]: m3_stream}, candle_row, {"4h"}, kraken, dry_run=False
        )
        assert stops == 1

    # --- write Model 3's own heartbeat too, exercising _write_last_run ---
    with engine.begin() as conn:
        _write_last_run(conn, m3_id, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))

    # --- assert Model 1's data is completely untouched ---
    with engine.connect() as conn:
        m1_lot_snapshot_after = dict(conn.execute(
            text("SELECT * FROM live.lots WHERE lot_id = :lid"), {"lid": m1_lot_id}
        ).mappings().fetchone())
        m1_heartbeat_after = conn.execute(
            text("SELECT last_run_at FROM live.executor_state WHERE model_id = :mid"), {"mid": m1_id}
        ).scalar()
        m1_lot_count = conn.execute(
            text("SELECT COUNT(*) FROM live.lots WHERE model_id = :mid"), {"mid": m1_id}
        ).scalar()

    assert m1_lot_snapshot_before == m1_lot_snapshot_after
    assert m1_heartbeat_before == m1_heartbeat_after   # Model 3's heartbeat write didn't touch Model 1's row
    assert m1_lot_count == 1   # no stray blended rows ever landed in live.lots

    print("\nIsolation OK -- full Model 3 cycle ran, Model 1's lot and heartbeat rows byte-for-byte unchanged.")
