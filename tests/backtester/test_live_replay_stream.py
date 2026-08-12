"""
Layer 2 -- run_live_replay_stream (src/backtester/live_replay_stream.py).

This is the piece that closes the last gap in docs/decisions/009: src/app/
stream_tester.py's interactive Run/Re-run buttons called engine.py's
run_backtest() directly -- the literal "separate backtester" the ADR's
mandate is about eliminating. This drives the real order_manager/
position_monitor code for a single, unlocked stream instead, so an
interactive design-time run and Model 1/2's validated live-replay numbers
now come from the same code path.

Hits local Postgres (a throwaway sandbox live.models row, sentinel 990,
cleaned up regardless of pass/fail) and real market_data -- needs a small,
known-active real window rather than mocked candles, since the whole point
is exercising the real indicator/signal/order_manager pipeline end to end.

Run:
    pytest tests/backtester/test_live_replay_stream.py -v -s
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.backtester.live_replay_stream import run_live_replay_stream
from src.backtester.metrics import compute_metrics


DIP_HUNTER_V2_PARAMS = {
    "filters": {"trend_context": {"require": "above", "sma_period": 200}},
    "position": {"trailing_stop_pct": 10.0, "entry_expiry_candles": 2},
    "sentiment": False,
    "core_params": {"drawdown_pct": 25.0, "rsi_recovery_threshold": 35.0},
    "core_signal": "rsi_recovery",
    "primary_timeframe": "1h",
}


def test_rejects_unsupported_slot_modes():
    for mode in ("blended", "cascade", "scale_down", "scale_up"):
        with pytest.raises(ValueError, match=mode):
            run_live_replay_stream({}, start="2023-01-01", end="2023-02-01",
                                   slot_count=1, slot_mode=mode, lot_size_usd=20.0)


def test_single_mode_runs_end_to_end_and_returns_run_backtest_shape():
    """Not a trade-count assertion (that's covered for real, against Model 1's
    actual locked config, by docs/decisions/009's Model 1/2 re-validation
    pass) -- this just proves the function runs clean over a real multi-year
    window and returns something compute_metrics can consume, since that's
    the contract src/app/stream_tester.py's payload/save_stream_test flow
    depends on."""
    result = run_live_replay_stream(
        DIP_HUNTER_V2_PARAMS, start="2022-01-01", end="2023-01-01",
        slot_count=1, slot_mode="single", stream_name="TEST Dip Hunter", lot_size_usd=20.0,
    )
    assert set(result.keys()) >= {"trades", "df", "start", "end", "signals", "maker_fee", "taker_fee"}
    trades = result["trades"]
    if not trades.empty:
        assert set(trades.columns) >= {
            "entry_ts", "exit_ts", "entry_price", "exit_price", "capital", "pnl", "exit_reason", "candles_held",
        }
    # Must not blow up compute_metrics -- the real contract this is standing in for
    m = compute_metrics(trades, 20.0, result["start"], result["end"])
    assert m["total_trades"] == len(trades)
