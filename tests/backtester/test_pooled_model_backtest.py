"""
run_pooled_model_backtest() (docs/decisions/008) -- deterministic unit tests
against small canned fixtures, not real market data. run_backtest() and
load_market_data() are monkeypatched so these tests are fast, isolated, and
don't depend on the local Postgres market_data table.

Flagged as a real gap in the 2026-08-07 Gauntlet's Part 4 code review (no
backtester-level test suite existed in this project at all before this
file) -- see tools/gauntlet_model2_pooled_bucket.py for the full real-data
Gauntlet run this complements, not replaces.

Run:
    pytest tests/backtester/test_pooled_model_backtest.py -v
"""
from unittest import mock

import pandas as pd
import pytest

from src.backtester import model_engine


def _trades(rows):
    return pd.DataFrame(rows, columns=["entry_ts", "exit_ts", "pnl"])


def _flat_market_data(start, end, price=50000.0):
    """A flat synthetic price series -- no dip ever occurs, so the BTC
    bucket's buy trigger never fires. Isolates the pool-walk math (what
    these tests target) from the bucket's real-price-path buy/sell logic,
    which is already exercised by the real Gauntlet run."""
    idx = pd.date_range(start, end, freq="1h")
    return pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": 1.0},
        index=idx,
    )


def _run(stream_a_trades, stream_b_trades, **kwargs):
    stream_configs = [
        {"stream_id": 1, "stream_name": "A", "params": {"primary_timeframe": "1h"},
         "lot_size_usd": 25.0, "slot_count": 1, "slot_mode": "single"},
        {"stream_id": 2, "stream_name": "B", "params": {"primary_timeframe": "1h"},
         "lot_size_usd": 25.0, "slot_count": 1, "slot_mode": "single"},
    ]

    def fake_run_backtest(params, start, end, slot_count, slot_mode, stream_name, lot_size_usd,
                          maker_fee=None, taker_fee=None):
        trades = stream_a_trades if stream_name == "A" else stream_b_trades
        return {"trades": trades}

    with mock.patch.object(model_engine, "run_backtest", side_effect=fake_run_backtest), \
         mock.patch.object(model_engine, "load_market_data",
                           return_value=_flat_market_data("2022-01-01", "2022-01-20")):
        return model_engine.run_pooled_model_backtest(
            stream_configs, start="2022-01-01", end="2022-01-20", **kwargs
        )


def test_pool_at_baseline_shrinks_next_entry_proportionally_after_a_loss():
    """Stream A loses $8 on its full $25 (pool 50 -> 42). Stream B's entry
    comes later, chronologically after A's exit -- pool is $42 < $50
    baseline, so B's $25-weighted (0.5) share should be $21, not $25."""
    trade_a = _trades([{"entry_ts": pd.Timestamp("2022-01-01"), "exit_ts": pd.Timestamp("2022-01-05"), "pnl": -8.0}])
    # roi is derived from pnl / the stream's CONFIGURED lot_size_usd (25), not
    # whatever capital ends up actually used -- pnl=2.5 here means roi=0.1.
    trade_b = _trades([{"entry_ts": pd.Timestamp("2022-01-10"), "exit_ts": pd.Timestamp("2022-01-15"), "pnl": 2.5}])

    res = _run(trade_a, trade_b)
    ledger = res["ledger"].set_index("stream_name")

    assert abs(ledger.loc["A", "capital_used"] - 25.0) < 1e-6   # pool was at baseline when A entered
    assert abs(ledger.loc["B", "capital_used"] - 21.0) < 1e-6   # 0.5 * (50 - 8)
    assert abs(ledger.loc["B", "pnl"] - (0.1 * 21.0)) < 1e-6    # roi (0.1) re-applied to the SHRUNK capital, not $25
    assert res["skipped_entries"] == 0
    assert res["halted_at"] is None


def test_entry_skipped_when_proportional_share_drops_below_ten():
    """weight=0.5, so pool must be >= $20 for a $10 share. starting_pool=$15
    -> share = $7.50 -- must skip entirely, no ledger row, no pool effect."""
    trade_a = _trades([{"entry_ts": pd.Timestamp("2022-01-01"), "exit_ts": pd.Timestamp("2022-01-05"), "pnl": 999.0}])
    trade_b = _trades([])

    res = _run(trade_a, trade_b, starting_pool=15.0)

    assert res["skipped_entries"] == 1
    assert res["ledger"].empty
    assert abs(res["final_pool_balance"] - 15.0) < 1e-6  # untouched -- the trade never happened


def test_halted_at_fires_the_instant_pool_crosses_hard_floor():
    """hard_floor here = 10.0 / max(weight) = 10.0 / 0.5 = 20.0. roi = -3/25
    = -0.12 (fixed by the canned pnl and the CONFIGURED lot_size_usd); the
    entry itself is sized off pool*weight since pool < baseline throughout,
    so pool_after = pool * (1 + roi * weight) = pool * 0.94 in every case
    below -- worked out by hand, not assumed to match a full-$25-sized loss."""
    trade_a = _trades([{"entry_ts": pd.Timestamp("2022-01-01"), "exit_ts": pd.Timestamp("2022-01-05"), "pnl": -3.0}])
    trade_b = _trades([])

    healthy = _run(trade_a, trade_b, starting_pool=25.0)   # capital=12.5, pool 25 -> 23.5, above floor 20
    assert healthy["halted_at"] is None

    halts_mid_run = _run(trade_a, trade_b, starting_pool=21.0)  # capital=10.5, pool 21 -> 19.74, crosses 20
    assert halts_mid_run["halted_at"] == pd.Timestamp("2022-01-05")

    already_halted = _run(trade_a, trade_b, starting_pool=15.0)  # already below floor at t=0
    assert already_halted["halted_at"] == "start"


def test_surplus_only_skim_matches_the_dynamic_skim_formula():
    """Single-stream case (weight=1.0): a trade that starts exactly at
    baseline and ends well above it should have its ENTIRE gain treated as
    surplus (surplus_before=0), skimmed at dynamic_skim's own rate formula
    -- verified against the same formula, not a hardcoded magic number."""
    stream_configs = [
        {"stream_id": 1, "stream_name": "Solo", "params": {"primary_timeframe": "1h"},
         "lot_size_usd": 100.0, "slot_count": 1, "slot_mode": "single"},
    ]
    trade = _trades([{"entry_ts": pd.Timestamp("2022-01-01"), "exit_ts": pd.Timestamp("2022-01-05"), "pnl": 50.0}])
    dynamic_skim = {"target_trades": 22, "avg_win_pct": 1.8, "min_skim_pct": 10.0, "max_skim_pct": 25.0}

    def fake_run_backtest(params, start, end, slot_count, slot_mode, stream_name, lot_size_usd,
                          maker_fee=None, taker_fee=None):
        return {"trades": trade}

    with mock.patch.object(model_engine, "run_backtest", side_effect=fake_run_backtest), \
         mock.patch.object(model_engine, "load_market_data",
                           return_value=_flat_market_data("2022-01-01", "2022-01-20")):
        res = model_engine.run_pooled_model_backtest(
            stream_configs, start="2022-01-01", end="2022-01-20", dynamic_skim=dynamic_skim,
        )

    pool_after = 150.0  # 100 baseline + 50 pnl, all of it surplus since pool started exactly at baseline
    raw_pct = 10.0 / ((1.8 / 100.0) * pool_after * 22) * 100.0
    expected_skim_pct = max(10.0, min(25.0, raw_pct))
    expected_skim = 50.0 * expected_skim_pct / 100.0

    assert abs(res["bucket"]["total_skimmed"] - expected_skim) < 1e-6
    assert abs(res["final_pool_balance"] - (150.0 - expected_skim)) < 1e-6


def test_rejects_multi_slot_streams():
    stream_configs = [
        {"stream_id": 1, "stream_name": "Bad", "params": {}, "lot_size_usd": 25.0,
         "slot_count": 2, "slot_mode": "staggered"},
    ]
    with pytest.raises(ValueError, match="single-slot"):
        model_engine.run_pooled_model_backtest(stream_configs, start="2022-01-01", end="2022-01-20")
