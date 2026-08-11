"""
Layer 1 -- run_model_backtest's fresh-run routing and optional stream-trade
cache reuse (docs/decisions/009 items #3/#7).

As of 2026-08-10, a cache MISS for a single/staggered stream at the default
fee rate now calls run_live_replay_stream (the real order_manager/
position_monitor path), not engine.py's run_backtest -- the "second
backtester" this ADR eliminates. run_backtest is now only a fallback for
slot modes live has no parity for (blended/cascade/scale_down/scale_up) or
a hypothetical fee-rate override (live_replay_stream always uses the real
current MAKER_FEE/TAKER_FEE, it has no override hook the way run_backtest
does).

Uses monkeypatch on model_engine._cached_stream_trades/run_backtest/
run_live_replay_stream rather than a real DB + pickle fixture or a real
multi-minute live-replay run -- those are already exercised for real
elsewhere this session (Model 1/2 re-validation, live_replay_stream's own
tests). This is about locking in the routing logic, the rescale math, and
the safe-by-default flag, which is the part that's easy to get subtly wrong.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.backtester import model_engine
from src.fees import MAKER_FEE, TAKER_FEE


def _fake_trades():
    return pd.DataFrame([
        {"entry_ts": pd.Timestamp("2023-01-01"), "exit_ts": pd.Timestamp("2023-01-02"),
         "entry_price": 100.0, "exit_price": 110.0, "capital": 20.0, "pnl": 2.0,
         "exit_reason": "trailing_stop", "candles_held": 1},
        {"entry_ts": pd.Timestamp("2023-02-01"), "exit_ts": pd.Timestamp("2023-02-02"),
         "entry_price": 100.0, "exit_price": 95.0, "capital": 20.0, "pnl": -1.0,
         "exit_reason": "stop_loss", "candles_held": 1},
    ])


def _fake_result():
    return {"trades": pd.DataFrame(), "df": None, "start": pd.Timestamp("2023-01-01"),
            "end": pd.Timestamp("2023-03-01"), "signals": pd.Series(dtype=bool),
            "maker_fee": MAKER_FEE, "taker_fee": TAKER_FEE}


def _stream_config(lot_size_usd, slot_mode="single"):
    return {
        "stream_id": 1, "stream_config_id": 42, "stream_name": "TEST Stream",
        "params": {"primary_timeframe": "1h", "position": {}, "filters": {}, "core_signal": "x"},
        "lot_size_usd": lot_size_usd, "slot_count": 1, "slot_mode": slot_mode,
    }


def test_use_cache_defaults_to_false_never_calls_cache_lookup(monkeypatch):
    called = []
    monkeypatch.setattr(model_engine, "_cached_stream_trades", lambda *a, **k: called.append(1) or None)
    monkeypatch.setattr(model_engine, "run_live_replay_stream", lambda *a, **k: _fake_result())
    model_engine.run_model_backtest([_stream_config(33.33)], start="2023-01-01", end="2023-03-01")
    assert called == [], "use_cache=False (the default) must never consult the cache"


def test_cache_hit_rescales_pnl_and_capital_to_this_models_allocation(monkeypatch):
    cached_capital = 20.0  # the stream-locking test's own placeholder capital
    model_capital = 33.33  # Model 1's real allocation for this stream
    monkeypatch.setattr(model_engine, "_cached_stream_trades",
                        lambda cid, start, end: (_fake_trades(), cached_capital))
    monkeypatch.setattr(model_engine, "run_live_replay_stream",
                        lambda *a, **k: pytest.fail("cache hit must not fall through to a fresh run"))
    monkeypatch.setattr(model_engine, "run_backtest",
                        lambda *a, **k: pytest.fail("cache hit must not fall through to a fresh run"))

    result = model_engine.run_model_backtest(
        [_stream_config(model_capital)], start="2023-01-01", end="2023-03-01", use_cache=True
    )
    trades = result["stream_results"][0]["trades"]
    scale = model_capital / cached_capital
    assert abs(trades["pnl"].iloc[0] - 2.0 * scale) < 1e-9
    assert abs(trades["pnl"].iloc[1] - (-1.0) * scale) < 1e-9
    assert abs(trades["capital"].iloc[0] - 20.0 * scale) < 1e-9
    # % return per trade is capital-independent -- rescaling must not touch price columns
    assert trades["entry_price"].iloc[0] == 100.0
    assert trades["exit_price"].iloc[0] == 110.0


def test_cache_miss_single_mode_default_fees_uses_live_replay_not_engine(monkeypatch):
    """The routing this session's model_engine.py change is actually about:
    a single-slot stream at the real fee rate must go through the live
    order_manager/position_monitor path, not engine.py's simulation."""
    monkeypatch.setattr(model_engine, "_cached_stream_trades", lambda *a, **k: None)
    live_calls, engine_calls = [], []
    monkeypatch.setattr(model_engine, "run_live_replay_stream",
                        lambda *a, **k: live_calls.append(1) or _fake_result())
    monkeypatch.setattr(model_engine, "run_backtest",
                        lambda *a, **k: engine_calls.append(1) or _fake_result())

    model_engine.run_model_backtest([_stream_config(33.33)], start="2023-01-01", end="2023-03-01", use_cache=True)
    assert live_calls == [1]
    assert engine_calls == []


def test_cache_miss_unsupported_slot_mode_falls_back_to_engine(monkeypatch):
    """blended/cascade/scale_down/scale_up have no live-replay path --
    must still fall back to engine.py's run_backtest, not error out."""
    monkeypatch.setattr(model_engine, "_cached_stream_trades", lambda *a, **k: None)
    live_calls, engine_calls = [], []
    monkeypatch.setattr(model_engine, "run_live_replay_stream",
                        lambda *a, **k: live_calls.append(1) or _fake_result())
    monkeypatch.setattr(model_engine, "run_backtest",
                        lambda *a, **k: engine_calls.append(1) or _fake_result())

    model_engine.run_model_backtest(
        [_stream_config(33.33, slot_mode="blended")], start="2023-01-01", end="2023-03-01", use_cache=True
    )
    assert live_calls == []
    assert engine_calls == [1]


def test_cache_miss_with_fee_override_falls_back_to_engine(monkeypatch):
    """live_replay_stream always uses the real current MAKER_FEE/TAKER_FEE --
    a caller asking for a hypothetical rate must still get engine.py, the
    only path that can actually honor the override."""
    monkeypatch.setattr(model_engine, "_cached_stream_trades", lambda *a, **k: None)
    live_calls, engine_calls = [], []
    monkeypatch.setattr(model_engine, "run_live_replay_stream",
                        lambda *a, **k: live_calls.append(1) or _fake_result())
    monkeypatch.setattr(model_engine, "run_backtest",
                        lambda *a, **k: engine_calls.append(1) or _fake_result())

    model_engine.run_model_backtest(
        [_stream_config(33.33)], start="2023-01-01", end="2023-03-01",
        use_cache=True, maker_fee=0.001, taker_fee=0.002,
    )
    assert live_calls == []
    assert engine_calls == [1]
