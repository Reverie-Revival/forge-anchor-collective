"""
Layer 4 — Orchestration test: exercises blended_executor.tick() itself, not
just the order_manager/position_monitor functions it calls. This is the
actual function GitHub Actions invokes every 30 minutes in production --
none of the other test files call it directly, so bugs in tick()'s own
glue logic (stream looping, the has_active_position gate, candle wiring,
double-entry prevention across repeated ticks) would have gone uncaught.

signal_engine.check() and _latest_candle_for_stream() both hit real market
data / real indicator computation -- monkeypatched here so this test is
deterministic and fast, not dependent on what BTC actually did recently.

Run:
    pytest tests/live/test_blended_executor_tick.py -v -s
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.live import blended_executor
from tests.live._fake_kraken import FakeKraken

load_dotenv()

TEST_MODEL_VERSION = 996

PARAMS = {
    "slots": {"slot_capital_weight": [20, 20, 20, 20, 20]},
    "filters": {}, "sentiment": False,
    "position": {"compound": True, "trailing_stop_pct": 5.0, "trail_arm_gain_pct": 4,
                "cumulative_drop_pcts": [1, 2, 5, 10], "entry_expiry_candles": 2,
                "capitulation_stop_pct": 15},
    "core_params": {"dip_pct": 1.0}, "core_signal": "fear_dip", "primary_timeframe": "4h",
}


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


@pytest.fixture
def sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(text("""
            INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
            VALUES (:v, 'TEST -- tick orchestration', 0, 'active') RETURNING model_id
        """), {"v": TEST_MODEL_VERSION}).scalar()
        stream_id = conn.execute(text("""
            INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                      parameters, slot_count, slot_mode, lot_size_usd)
            VALUES (:mid, 'TEST Grid Stacker Blended', 'v8', 'blended_dca', CAST(:p AS jsonb), 5, 'blended', 20.0)
            RETURNING stream_id
        """), {"mid": model_id, "p": json.dumps(PARAMS)}).scalar()
        conn.execute(text("INSERT INTO live.blended_capital (model_id, available_capital) VALUES (:mid, 100.00)"),
                     {"mid": model_id})
        conn.execute(text("INSERT INTO live.executor_state (id, last_run_at, model_id) VALUES (:id, now(), :mid)"),
                     {"id": model_id, "mid": model_id})

    yield engine, stream_id, model_id

    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM live.blended_fills WHERE position_id IN
            (SELECT position_id FROM live.blended_positions WHERE model_id = :mid)
        """), {"mid": model_id})
        conn.execute(text("DELETE FROM live.blended_positions WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.blended_capital WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.executor_runs WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.executor_state WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def _load_streams(engine, model_id):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT stream_id, model_id, stream_name, stream_version, strategy_type,
                   parameters, slot_count, slot_mode, lot_size_usd
            FROM live.streams WHERE model_id = :mid
        """), {"mid": model_id}).fetchall()
    return {r.stream_id: dict(r._mapping) for r in rows}


def test_tick_places_entry_when_signal_fires(sandbox, monkeypatch):
    """The actual production entry point: tick() sees a closed 4h candle,
    a firing signal, and no active position -- it must place slot 1."""
    engine, stream_id, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    monkeypatch.setattr(blended_executor.signal_engine, "check", lambda stream: True)
    monkeypatch.setattr(blended_executor, "_latest_candle_for_stream",
                        lambda stream: {"close": 50000.0, "low": 49500.0})

    streams = _load_streams(engine, model_id)
    now = datetime.now(timezone.utc)
    last_tick = now - timedelta(hours=5)   # guarantees a 4h boundary crossed

    with engine.begin() as conn:
        blended_executor.tick(conn, streams, kraken, last_tick, now, dry_run=False)
        assert conn.execute(text(
            "SELECT COUNT(*) FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream_id}).scalar() == 1
        status = conn.execute(text(
            "SELECT status FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream_id}).scalar()
        # FakeKraken fills instantly by default, so a single tick both places
        # AND confirms the fill (tick() checks pending fills in the same pass
        # it places new entries) -- either active state proves the entry
        # order actually went out.
        assert status in ("PENDING_ENTRY", "OPEN")
        assert len(kraken.orders) == 1

    print("\ntick() OK -- placed slot-1 entry on a firing signal.")


def test_tick_does_not_double_enter_across_repeated_ticks(sandbox, monkeypatch):
    """The signal stays 'on' for multiple ticks in a row (very plausible --
    a dip condition can span several candles). tick() must never place a
    second slot-1 entry while one is already building/open."""
    engine, stream_id, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0

    monkeypatch.setattr(blended_executor.signal_engine, "check", lambda stream: True)
    monkeypatch.setattr(blended_executor, "_latest_candle_for_stream",
                        lambda stream: {"close": 50000.0, "low": 49500.0})

    streams = _load_streams(engine, model_id)
    now = datetime.now(timezone.utc)
    last_tick = now - timedelta(hours=5)

    with engine.begin() as conn:
        blended_executor.tick(conn, streams, kraken, last_tick, now, dry_run=False)

    # second tick, same firing signal, one 4h period later
    now2 = now + timedelta(hours=4, minutes=5)
    with engine.begin() as conn:
        blended_executor.tick(conn, streams, kraken, now, now2, dry_run=False)
        count = conn.execute(text(
            "SELECT COUNT(*) FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream_id}).scalar()
        assert count == 1   # still exactly one position, not two

    print("\ntick() OK -- second tick with the same firing signal did not double-enter.")


def test_tick_survives_kraken_exception_mid_poll(sandbox, monkeypatch):
    """A network blip mid-poll must not crash the tick or corrupt state --
    it should log and leave the position PENDING for the next tick to retry.
    Uses next_fill_mode='none' so the order is genuinely still pending going
    into the second tick (a real Kraken order can sit unfilled for a while) --
    otherwise FakeKraken's instant-fill default would flip it to OPEN before
    the exception path is ever exercised."""
    engine, stream_id, model_id = sandbox
    kraken = FakeKraken()
    kraken._next_price = 50000.0
    kraken.next_fill_mode = "none"

    monkeypatch.setattr(blended_executor.signal_engine, "check", lambda stream: True)
    monkeypatch.setattr(blended_executor, "_latest_candle_for_stream",
                        lambda stream: {"close": 50000.0, "low": 49500.0})

    streams = _load_streams(engine, model_id)
    now = datetime.now(timezone.utc)
    last_tick = now - timedelta(hours=5)

    with engine.begin() as conn:
        blended_executor.tick(conn, streams, kraken, last_tick, now, dry_run=False)
        status = conn.execute(text(
            "SELECT status FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream_id}).scalar()
        assert status == "PENDING_ENTRY"   # confirmed still pending before the exception path

    def _raise(*a, **kw):
        raise ConnectionError("simulated network blip")
    monkeypatch.setattr(kraken, "get_order_status", _raise)
    monkeypatch.setattr(blended_executor.signal_engine, "check", lambda stream: False)  # no new signal this tick

    now2 = now + timedelta(minutes=30)   # no new 4h candle -- just a normal 30-min poll tick
    with engine.begin() as conn:
        blended_executor.tick(conn, streams, kraken, now, now2, dry_run=False)   # must not raise
        status = conn.execute(text(
            "SELECT status FROM live.blended_positions WHERE stream_id = :sid"
        ), {"sid": stream_id}).scalar()
        assert status == "PENDING_ENTRY"   # untouched, ready to retry next tick

    print("\ntick() OK -- survived a Kraken exception mid-poll without losing state.")
