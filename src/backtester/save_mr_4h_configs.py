"""
Save both 4h MR configs so they're viewable in the Stream Tester.
  - 4h + 5% stop  → saved to DB (appears in sidebar as a named run)
  - 4h + 7% stop  → written to .last_run.pkl (appears as active unsaved run)
Run: python -m src.backtester.save_mr_4h_configs
"""
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold
from src.app.db import get_engine, LAST_RUN_PATH, save_stream_test, load_stream_history

engine = get_engine()
with engine.connect() as conn:
    row = conn.execute(text(
        "SELECT preset_id, start_date, end_date FROM timeframe_presets "
        "WHERE name = 'Primary Window' AND is_active = TRUE"
    )).fetchone()
PRESET_ID = row[0]
START     = str(row[1])
END       = str(row[2])

BASE_PARAMS = {
    "primary_timeframe": "4h",
    "core_signal": "ema_crossover",
    "core_params": {"ema_short": 20, "ema_long": 50},
    "filters": {
        "trend_context": {"sma_period": 200, "require": "above"},
        "rsi": {"period": 14, "min": 55, "max": None},
    },
    "sentiment": {"fear_greed": {"min": 25, "max": None}},
    "position": {
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

import copy

def build_params(trail):
    p = copy.deepcopy(BASE_PARAMS)
    p["position"]["trailing_stop_pct"] = trail
    return p

def run_and_build_payload(params, stream_name, lot=10.0):
    result          = run_backtest(params, start=START, end=END,
                                   slot_count=1, slot_mode="single",
                                   stream_name=stream_name, lot_size_usd=lot)
    trades          = result["trades"]
    initial_capital = lot
    metrics         = compute_metrics(trades, initial_capital, result["start"], result["end"])
    ending_balance  = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)
    bh              = btc_buy_and_hold(result["df"], initial_capital)
    payload = {
        "stream_name":     stream_name,
        "params":          params,
        "result":          result,
        "trades":          trades,
        "df":              result["df"],
        "metrics":         metrics,
        "bh":              bh,
        "initial_capital": initial_capital,
        "ending_balance":  ending_balance,
        "slot_count":      1,
        "slot_mode":       "single",
        "lot_size_usd":    lot,
    }
    return payload, result, metrics, initial_capital, ending_balance


def print_summary(label, metrics):
    print(f"  Ann. return  : {metrics['annualized_return_pct']:+.1f}%")
    print(f"  Trades       : {metrics['total_trades']}")
    print(f"  Win rate     : {metrics['win_rate']:.0%}")
    print(f"  Max DD       : {metrics['max_drawdown_pct']:.1f}%")
    print(f"  Profit factor: {metrics['profit_factor']:.2f}")


# ── Config A: 4h + 5% stop → save to DB ──────────────────────────────────────
params_a = build_params(trail=5.0)
print(f"\nRunning 4h + 5% stop (will save to DB)...")
payload_a, result_a, metrics_a, ic_a, eb_a = run_and_build_payload(params_a, "Momentum Rider v1")
print_summary("4h + 5% stop", metrics_a)

history = load_stream_history()
test_id, run_num = save_stream_test(
    stream_name="Momentum Rider v1",
    params=params_a,
    result=result_a,
    metrics=metrics_a,
    initial_capital=ic_a,
    ending_balance=eb_a,
    payload=payload_a,
    preset_id=PRESET_ID,
    notes="4h candles — cleaner signal, less 1h noise. 5% trailing stop.",
    history=history,
)
print(f"  → Saved as test_id={test_id}, run #{run_num}")


# ── Config B: 4h + 7% stop → write to .last_run.pkl ─────────────────────────
params_b = build_params(trail=7.0)
print(f"\nRunning 4h + 7% stop (will load as active unsaved run)...")
payload_b, result_b, metrics_b, ic_b, eb_b = run_and_build_payload(params_b, "Momentum Rider v1")
print_summary("4h + 7% stop", metrics_b)

with open(LAST_RUN_PATH, "wb") as f:
    pickle.dump(payload_b, f)
print(f"  → Written to .last_run.pkl (shows as unsaved run in Stream Tester)")

print(f"\nDone. In the Stream Tester:")
print(f"  • Select 'Momentum Rider v1' → Run #{run_num} (sidebar) to see 4h + 5%")
print(f"  • The unsaved run at the bottom of the run list is 4h + 7%")
