"""
Confirms blended_executor.py's _load_streams() -- the one function that
actually reads the LIVE_MODEL_VERSION constant -- correctly resolves Model
4's real model_version (4) against a live.models/live.streams row. Nothing
else in tests/live/ exercises this specific lookup: the tick() tests pass a
streams dict directly, bypassing it entirely.

Run:
    pytest tests/live/test_blended_executor_model4_lookup.py -v
"""
import json

import pytest
from sqlalchemy import text

from src.live import blended_executor
from tests.live.conftest import get_local_engine as _get_engine

PARAMS = {
    "slots": {"slot_capital_weight": [20, 20, 20, 20, 20]},
    "filters": {}, "sentiment": {"fear_greed": {}},
    "position": {
        "compound": True, "trailing_stop_pct": 5.0, "trail_arm_gain_pct": 4,
        "cumulative_drop_pcts": [1, 2, 5, 10], "entry_expiry_candles": 2,
        "capitulation_ladder_pcts": [20, 22, 24, 26, 28],
        "capitulation_ladder_final_cut_pct": 30,
        "sentiment_tilt": {"direction": -1, "strength": 0.4},
        "slot_promotion_days": [3, 6, 9, 12], "max_promotions_per_position": 1,
        "shallow_breakeven_margin_pct": 1.0, "shallow_slot_threshold": 5,
    },
    "core_params": {"dip_pct": 1.0}, "core_signal": "fear_dip", "primary_timeframe": "4h",
}


@pytest.fixture
def model4_row():
    """Seeds a real live.models row at model_version=4 (blended_executor.py's
    actual LIVE_MODEL_VERSION constant) -- local Postgres's live schema is a
    pure test sandbox, never real production data, so this is safe."""
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM live.streams WHERE model_id IN
            (SELECT model_id FROM live.models WHERE model_version = 4)
        """))
        conn.execute(text("DELETE FROM live.models WHERE model_version = 4"))

        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (4, 'TEST -- GS: Reflex lookup check', 0, 'active') RETURNING model_id
        """)).scalar()
        stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'GS: Reflex', 'v2', 'blended_dca', CAST(:p AS jsonb), 5, 'blended', 20.0)
            RETURNING stream_id
        """), {"mid": model_id, "p": json.dumps(PARAMS)}).scalar()

    yield engine, model_id, stream_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def test_load_streams_resolves_model4_by_real_version_constant(model4_row):
    engine, model_id, stream_id = model4_row
    with engine.connect() as conn:
        streams = blended_executor._load_streams(conn)

    assert stream_id in streams, (
        f"blended_executor.LIVE_MODEL_VERSION={blended_executor.LIVE_MODEL_VERSION} "
        f"did not resolve the seeded model_version=4 stream -- got {list(streams.keys())}"
    )
    found = streams[stream_id]
    assert found["model_id"] == model_id
    assert found["stream_name"] == "GS: Reflex"
    assert found["slot_mode"] == "blended"
    assert found["slot_count"] == 5
