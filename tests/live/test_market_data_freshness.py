"""
Tests for the market_data freshness guard (_ensure_market_data_fresh in
executor.py), shared by both Model 1's executor.tick() and Model 3's
blended_executor.tick().

Closes a real timing race: the executor reads whatever's in market_data and
trusts a candle is "closed" purely on wall-clock time (see
_detect_closed_timeframes), but resample_ohlcv's dropna() only drops
fully-empty bins -- a bin missing its last 15m bar still produces a candle,
just built from incomplete data. If market_data_updater's cron hasn't landed
that bar yet when the executor ticks, a signal or trailing-stop check could
fire off a wrong close/high/low. This guard blocks briefly for the data to
catch up and raises (with a real alert) if it never does, rather than
silently acting on stale data.
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from src.live import executor


def _fake_conn(scalar_values):
    conn = MagicMock()
    conn.execute.return_value.scalar.side_effect = scalar_values
    return conn


def test_no_check_when_no_boundary_closed():
    conn = _fake_conn([])
    executor._ensure_market_data_fresh(conn, datetime.now(timezone.utc), set(), model_id=1)
    conn.execute.assert_not_called()


def test_passes_immediately_when_data_already_fresh():
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    latest = datetime(2026, 8, 3, 11, 45, tzinfo=timezone.utc)  # exactly the required bar
    conn = _fake_conn([latest])

    with patch("time.sleep") as sleep_mock:
        executor._ensure_market_data_fresh(conn, now, {"1h"}, model_id=1)

    sleep_mock.assert_not_called()
    assert conn.execute.call_count == 1


def test_retries_then_succeeds():
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    stale = datetime(2026, 8, 3, 11, 30, tzinfo=timezone.utc)
    fresh = datetime(2026, 8, 3, 11, 45, tzinfo=timezone.utc)
    conn = _fake_conn([stale, stale, fresh])

    with patch("time.sleep") as sleep_mock:
        executor._ensure_market_data_fresh(conn, now, {"1h"}, model_id=1)

    assert sleep_mock.call_count == 2
    assert conn.execute.call_count == 3


@patch("src.live.executor.notifier.alert_market_data_stale")
def test_raises_and_alerts_after_exhausting_retries(alert_mock):
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    stale = datetime(2026, 8, 3, 11, 30, tzinfo=timezone.utc)
    conn = _fake_conn([stale] * (executor.MARKET_DATA_MAX_RETRIES + 1))

    with patch("time.sleep") as sleep_mock:
        with pytest.raises(executor.MarketDataStaleError):
            executor._ensure_market_data_fresh(conn, now, {"1h"}, model_id=1)

    assert sleep_mock.call_count == executor.MARKET_DATA_MAX_RETRIES
    alert_mock.assert_called_once()
    called_model_id, called_latest, called_expected = alert_mock.call_args[0]
    assert called_model_id == 1
    assert called_latest == stale


def test_null_latest_timestamp_treated_as_stale():
    """An empty market_data table (MAX returns NULL) must not be mistaken
    for fresh data -- None >= expected would raise a TypeError if not
    guarded, so confirm it's treated as stale instead."""
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    conn = _fake_conn([None] * (executor.MARKET_DATA_MAX_RETRIES + 1))

    with patch("time.sleep"):
        with pytest.raises(executor.MarketDataStaleError):
            executor._ensure_market_data_fresh(conn, now, {"1h"}, model_id=1)
