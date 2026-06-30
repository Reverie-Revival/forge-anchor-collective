"""
MR exploration batch — runs multiple configs and prints a comparison table.
Does NOT save to DB or .last_run.pkl — read-only exploration.
Run: python -m src.backtester.explore_mr
"""
import copy
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics

MR_BASE = {
    "primary_timeframe": "1h",
    "core_signal": "ema_crossover",
    "core_params": {"ema_short": 20, "ema_long": 50},
    "filters": {
        "trend_context": {"sma_period": 200, "require": "above"},
        "rsi": {"period": 14, "min": 55, "max": None},
    },
    "sentiment": {"fear_greed": {"min": 25, "max": None}},
    "position": {
        "trailing_stop_pct": 5.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

from src.app.db import get_engine
engine = get_engine()
with engine.connect() as conn:
    row = conn.execute(text(
        "SELECT start_date, end_date FROM timeframe_presets WHERE name = 'Primary Window' AND is_active = TRUE"
    )).fetchone()
START, END = str(row[0]), str(row[1])


def run(label, params, slot_count=1, slot_mode="single", lot=10.0):
    result  = run_backtest(params, start=START, end=END,
                           slot_count=slot_count, slot_mode=slot_mode,
                           stream_name="MR", lot_size_usd=lot)
    metrics = compute_metrics(result["trades"], lot * slot_count, result["start"], result["end"])
    return label, metrics


def p(**overrides):
    """Deep-copy base params and apply overrides."""
    params = copy.deepcopy(MR_BASE)
    for key, val in overrides.items():
        if key == "trail":
            params["position"]["trailing_stop_pct"] = val
        elif key == "rsi_min":
            params["filters"]["rsi"]["min"] = val
        elif key == "rsi_max":
            params["filters"]["rsi"]["max"] = val
        elif key == "ema":
            params["core_params"]["ema_short"] = val[0]
            params["core_params"]["ema_long"]  = val[1]
        elif key == "tf":
            params["primary_timeframe"] = val
        elif key == "min_hold":
            params["position"]["min_hold_candles"] = val
        elif key == "fg_min":
            params["sentiment"]["fear_greed"]["min"] = val
        elif key == "no_rsi":
            params["filters"].pop("rsi", None)
    return params


experiments = [
    # label                          params
    ("baseline  5% stop",            p()),
    # trailing stop sweep
    ("trail 7%",                     p(trail=7.0)),
    ("trail 8%",                     p(trail=8.0)),
    ("trail 9%",                     p(trail=9.0)),
    ("trail 10%",                    p(trail=10.0)),
    # min hold (prevent early shakeout) — candles on 1h TF
    ("trail 7% + min_hold 12h",      p(trail=7.0, min_hold=12)),
    ("trail 7% + min_hold 24h",      p(trail=7.0, min_hold=24)),
    ("trail 8% + min_hold 12h",      p(trail=8.0, min_hold=12)),
    # RSI tuning
    ("RSI min 50",                   p(rsi_min=50)),
    ("RSI min 60",                   p(rsi_min=60)),
    ("no RSI filter",                p(no_rsi=True)),
    # EMA pairs
    ("EMA 10/30",                    p(ema=(10, 30))),
    ("EMA 20/100",                   p(ema=(20, 100))),
    # Timeframe
    ("4h candles",                   p(tf="4h")),
    ("4h + trail 7%",                p(tf="4h", trail=7.0)),
    ("4h + trail 8%",                p(tf="4h", trail=8.0)),
    # Combinations with best trail
    ("trail 7% + RSI 50",            p(trail=7.0, rsi_min=50)),
    ("trail 7% + RSI 60",            p(trail=7.0, rsi_min=60)),
    ("trail 8% + RSI 50",            p(trail=8.0, rsi_min=50)),
    ("trail 8% + min_hold 24h",      p(trail=8.0, min_hold=24)),
]

print(f"\n{'Label':<30} {'Ann%':>7} {'Trades':>7} {'WR%':>6} {'MaxDD%':>8} {'PF':>6}")
print("-" * 70)

results = []
for label, params in experiments:
    _, m = run(label, params)
    ann  = m.get("annualized_return_pct", 0) or 0
    tr   = m.get("total_trades", 0)
    wr   = (m.get("win_rate", 0) or 0) * 100
    dd   = m.get("max_drawdown_pct", 0) or 0
    pf   = m.get("profit_factor", 0) or 0
    results.append((label, ann, tr, wr, dd, pf))
    print(f"{label:<30} {ann:>+7.1f}% {tr:>7} {wr:>5.0f}% {dd:>7.1f}% {pf:>6.2f}")

print("\n── Top 5 by Ann. Return ──")
for row in sorted(results, key=lambda x: x[1], reverse=True)[:5]:
    label, ann, tr, wr, dd, pf = row
    print(f"  {label:<30} {ann:+.1f}%  trades={tr}  DD={dd:.1f}%  PF={pf:.2f}")
