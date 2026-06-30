"""
Run both 4h MR configs across all 4 presets and save to DB.
  - Run #2: 4h + 5% stop (Primary already saved as test_id=13; adds the other 3)
  - Run #3: 4h + 7% stop (all 4 presets)
After this, Stream Tester shows MR v1 with runs #1 (baseline), #2 (4h/5%), #3 (4h/7%).
Run: python -m src.backtester.save_mr_4h_all_presets
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
from src.backtester.metrics import compute_metrics, btc_buy_and_hold
from src.app.db import get_engine, save_stream_test, load_stream_history

engine = get_engine()
with engine.connect() as conn:
    presets = conn.execute(text(
        "SELECT preset_id, name, start_date, end_date FROM timeframe_presets "
        "WHERE is_active = TRUE ORDER BY start_date"
    )).fetchall()

BASE = {
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

CONFIGS = [
    {"trail": 5.0, "notes": "4h candles — cleaner signal, less 1h noise. 5% trailing stop."},
    {"trail": 7.0, "notes": "4h candles — wider stop to capture longer trend moves. 7% trailing stop."},
]


def build_params(trail):
    p = copy.deepcopy(BASE)
    p["position"]["trailing_stop_pct"] = trail
    return p


def run_and_save(params, preset, notes, history):
    preset_id  = preset[0]
    label      = preset[1]
    start      = str(preset[2])
    end        = str(preset[3]) if preset[3] else None

    result          = run_backtest(params, start=start, end=end,
                                   slot_count=1, slot_mode="single",
                                   stream_name="Momentum Rider v1", lot_size_usd=10.0)
    trades          = result["trades"]
    initial_capital = 10.0
    metrics         = compute_metrics(trades, initial_capital, result["start"], result["end"])
    ending_balance  = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)
    bh              = btc_buy_and_hold(result["df"], initial_capital)

    payload = {
        "stream_name":     "Momentum Rider v1",
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
        "lot_size_usd":    10.0,
    }

    test_id, run_num = save_stream_test(
        stream_name="Momentum Rider v1",
        params=params,
        result=result,
        metrics=metrics,
        initial_capital=initial_capital,
        ending_balance=ending_balance,
        payload=payload,
        preset_id=preset_id,
        notes=notes,
        history=history,
    )

    ann = metrics.get("annualized_return_pct") or 0
    tr  = metrics.get("total_trades", 0)
    dd  = metrics.get("max_drawdown_pct") or 0
    pf  = metrics.get("profit_factor") or 0
    print(f"    {label:<22} {ann:>+7.1f}%  trades={tr:>3}  DD={dd:>6.1f}%  PF={pf:.2f}  → test_id={test_id} run #{run_num}")
    return run_num


print(f"\n{'='*70}")
for cfg in CONFIGS:
    params = build_params(cfg["trail"])
    trail  = cfg["trail"]
    print(f"\n4h + {trail}% trailing stop")
    print(f"{'-'*70}")

    # Reload history fresh each config so run_number logic sees prior saves
    history = load_stream_history()

    # Skip Primary for 4h+5% (already saved as test_id=13)
    for preset in presets:
        label = preset[1]
        if trail == 5.0 and label == "Primary Window":
            print(f"    {'Primary Window':<22}  (already saved as test_id=13 — skipping)")
            continue
        run_and_save(params, preset, cfg["notes"], history)
        history = load_stream_history()  # refresh so next preset sees current run numbers

print(f"\n{'='*70}")
print("\nAll done. In the Stream Tester, select 'Momentum Rider v1' and choose:")
print("  Run #2 — 4h / 5% stop (view all 4 windows)")
print("  Run #3 — 4h / 7% stop (view all 4 windows)")
