"""
Layer 2 -- Model 1 order manager state machine integration test.

Model 1 previously had zero dedicated tests for order_manager.py/
position_monitor.py. Added alongside the real-fee-capture fix (see
HANDOFF.md) since that change rewrote place_exit's P&L formula for real
money -- this test exercises the full PENDING -> OPEN -> CLOSED cycle
against a fake KrakenClient so the new formula is verified without touching
a real Kraken account or real money.

Runs against local Postgres only (DATABASE_URL) using a throwaway
model_version=999 row, cleaned up at the end regardless of pass/fail.

This test MUST pass before any commit that touches order_manager.py or
position_monitor.py.

Run:
    pytest tests/live/test_order_manager_state_machine.py -v -s
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv
from sqlalchemy import text

from src.live import order_manager
from src.live import position_monitor
from tests.live._fake_kraken import FakeKraken
from tests.live.conftest import get_local_engine as _get_engine

load_dotenv()

TEST_MODEL_VERSION = 998

PARAMS = {
    "filters": {},
    "position": {"trailing_stop_pct": 5.0, "entry_expiry_candles": 2},
    "sentiment": False,
    "core_params": {},
    "core_signal": "test_signal",
    "primary_timeframe": "1h",
}


@pytest.fixture
def sandbox():
    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.models WHERE model_version = :v"), {"v": TEST_MODEL_VERSION})
        model_id = conn.execute(
            text("""
                INSERT INTO live.models (model_version, description, based_on_model_test_id, status)
                VALUES (:v, 'TEST -- order manager state machine', 0, 'active')
                RETURNING model_id
            """),
            {"v": TEST_MODEL_VERSION},
        ).scalar()
        stream_id = conn.execute(
            text("""
                INSERT INTO live.streams (model_id, stream_name, stream_version, strategy_type,
                                          parameters, slot_count, slot_mode, lot_size_usd)
                VALUES (:mid, 'TEST Momentum Rider', 'v1', 'ema_crossover',
                        CAST(:params AS jsonb), 1, 'single', 20.0)
                RETURNING stream_id
            """),
            {"mid": model_id, "params": __import__("json").dumps(PARAMS)},
        ).scalar()

    stream = {
        "stream_id": stream_id, "model_id": model_id, "stream_name": "TEST Momentum Rider",
        "parameters": PARAMS, "lot_size_usd": 20.0,
    }

    yield engine, stream, model_id

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM live.lots WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.streams WHERE model_id = :mid"), {"mid": model_id})
        conn.execute(text("DELETE FROM live.models WHERE model_id = :mid"), {"mid": model_id})


def test_full_cycle_entry_trailing_stop_exit_uses_real_fees(sandbox):
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        assert order_manager.slot_is_available(conn, stream["stream_id"], 1)
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
        assert not order_manager.slot_is_available(conn, stream["stream_id"], 1)

    with engine.begin() as conn:
        fills, expirations = order_manager.check_pending(conn, kraken, dry_run=False)
        assert fills == 1 and expirations == 0
        lot = conn.execute(text("""
            SELECT status, entry_price, entry_fee_usd, btc_quantity, opening_capital
            FROM live.lots WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert lot.status == "OPEN"
        assert float(lot.entry_price) == 50000.0
        # FakeKraken's fee model is 0.4% of notional -- capital ($20) * 0.004
        assert abs(float(lot.entry_fee_usd) - 20.0 * 0.004) < 1e-6

    # Rally past the trail, then pierce the 5% trailing stop.
    armed_close = 50000.0 * 1.05
    kraken._next_price = armed_close   # ticker reflects the market at exit time
    stop_trigger_low = armed_close * (1 - 0.05) - 1
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"1h"}, kraken, dry_run=False
        )
        assert stops == 1
        lot = conn.execute(text("""
            SELECT status, exit_reason, exit_price, exit_fee_usd, realized_pnl, fee_is_estimated,
                   entry_price, entry_fee_usd, opening_capital
            FROM live.lots WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert lot.status == "CLOSED"
        assert lot.exit_reason == "trailing_stop"
        assert lot.fee_is_estimated is False   # FakeKraken confirms the fill on the first poll
        assert abs(float(lot.exit_price) - armed_close) < 0.01

        # FakeKraken's fee model is 0.4% of exit notional (qty * exit_price),
        # which is capital scaled by the price move since entry -- not flat $20*0.004.
        exit_notional = 20.0 * (armed_close / 50000.0)
        expected_exit_fee = exit_notional * 0.004
        assert abs(float(lot.exit_fee_usd) - expected_exit_fee) < 1e-4

        gain = (float(lot.exit_price) - float(lot.entry_price)) / float(lot.entry_price)
        expected_pnl = 20.0 * gain - float(lot.entry_fee_usd) - expected_exit_fee
        assert abs(float(lot.realized_pnl) - expected_pnl) < 0.01

    print(f"\nModel 1 full cycle OK -- entry -> trailing stop exit. pnl=${float(lot.realized_pnl):.2f}")


def test_exit_falls_back_to_estimate_when_fill_not_yet_confirmed(sandbox):
    """Mirrors the Model 3 fallback test -- if the synchronous post-placement
    poll doesn't confirm the market sell's fill yet, the lot must still close
    (using the estimate) instead of hanging, flagged for manual reconciliation."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending(conn, kraken, dry_run=False)

    armed_close = 50000.0 * 1.05
    stop_trigger_low = armed_close * (1 - 0.05) - 1
    kraken._next_price = armed_close
    kraken.next_fill_mode = "delayed"
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"1h"}, kraken, dry_run=False
        )
        assert stops == 1
        lot = conn.execute(text("""
            SELECT status, exit_price, fee_is_estimated FROM live.lots WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert lot.status == "CLOSED"   # never gets stuck even though the fill wasn't confirmed
        assert lot.fee_is_estimated is True
        # Fallback uses the passed-in stop price, not the real (unconfirmed) ticker fill
        assert float(lot.exit_price) != armed_close


def test_exit_with_legacy_null_entry_fee_falls_back_to_estimate(sandbox):
    """Real-world scenario this fix deploys into: every currently-OPEN Model 1
    lot in production was opened before this migration, so entry_fee_usd is
    NULL for all of them. The very next trailing-stop exit for any of those
    lots must fall back to a MAKER_FEE-based estimate for the entry side
    (while still using the real, confirmed exit fee) and flag fee_is_estimated,
    not silently treat the missing entry fee as zero."""
    engine, stream, model_id = sandbox
    kraken = FakeKraken()

    kraken._next_price = 50000.0
    with engine.begin() as conn:
        order_manager.place_entry(conn, stream, kraken, dry_run=False)
    with engine.begin() as conn:
        order_manager.check_pending(conn, kraken, dry_run=False)

    # --- simulate a lot that predates this fix ---
    with engine.begin() as conn:
        conn.execute(text("UPDATE live.lots SET entry_fee_usd = NULL WHERE stream_id = :sid"),
                     {"sid": stream["stream_id"]})

    armed_close = 50000.0 * 1.05
    stop_trigger_low = armed_close * (1 - 0.05) - 1
    kraken._next_price = armed_close   # this exit DOES get a real, confirmed fill
    with engine.begin() as conn:
        candle_row = {stream["stream_id"]: {"close": armed_close, "low": stop_trigger_low}}
        stops = position_monitor.check_all(
            conn, {stream["stream_id"]: stream}, candle_row, {"1h"}, kraken, dry_run=False
        )
        assert stops == 1
        lot = conn.execute(text("""
            SELECT exit_price, exit_fee_usd, realized_pnl, fee_is_estimated, entry_price
            FROM live.lots WHERE stream_id = :sid
        """), {"sid": stream["stream_id"]}).fetchone()
        assert lot.fee_is_estimated is True   # entry side is an estimate, even though exit is real
        assert abs(float(lot.exit_price) - armed_close) < 0.01   # exit fill IS the real one

        expected_entry_fee_estimate = 20.0 * order_manager.MAKER_FEE
        exit_notional = 20.0 * (armed_close / 50000.0)
        expected_exit_fee = exit_notional * 0.004   # FakeKraken's real fee model
        gain = (armed_close - 50000.0) / 50000.0
        expected_pnl = 20.0 * gain - expected_entry_fee_estimate - expected_exit_fee
        assert abs(float(lot.realized_pnl) - expected_pnl) < 0.01
