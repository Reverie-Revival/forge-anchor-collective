"""
Lock BS v2 (stream_id=3) and run Model 1 Run #4.
Stream test rows already saved as test_ids 35-39.

Run: python -m src.backtester.run_model_r4
"""
import json, pickle, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv; load_dotenv()

from sqlalchemy import text
from src.backtester.model_runner import run_model, MODEL_LAST_RUN_PATH
from src.app.db import get_engine, save_model_test

BS_V2_PARAMS = {
    "primary_timeframe": "1h",
    "core_signal": "range_breakout",
    "core_params": {"breakout_lookback": 24},
    "filters": {
        "bollinger": {"period": 20, "std_dev": 2.0, "squeeze": {"max_bandwidth_pct": 6.0}},
        "atr_regime": {"period": 14, "avg_period": 30, "max_pct_of_avg": 90},
        "breakout_candle": {"body_ratio_min": 0.4, "close_position_min": 0.6},
        "trend_context": {"sma_period": 200, "require": "above"},
    },
    "sentiment": {"fear_greed": {"min": 55}},
    "position": {
        "trailing_stop_pct": 10.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

ALLOCATION = {
    "Momentum Rider v2": {"lot_size_usd": 33.33, "slot_count": 1, "slot_mode": "single"},
    "Dip Hunter v2":     {"lot_size_usd": 33.33, "slot_count": 1, "slot_mode": "single"},
    "Breakout Scout v2": {"lot_size_usd": 33.33, "slot_count": 1, "slot_mode": "single"},
}

engine = get_engine()

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT preset_id, name, start_date, end_date FROM timeframe_presets WHERE is_active=TRUE"
    )).fetchall()
presets = {r.name: {"preset_id": r.preset_id, "start": str(r.start_date), "end": str(r.end_date) if r.end_date else None}
           for r in rows}

# ── 1. Lock already completed — stream_id=3 locked to test_id=35 ─────────────
print("\n── stream_id=3 already locked as Breakout Scout v2 (test_id=35) ─────────")

# ── 2. Run Model #4 across all presets ───────────────────────────────────────
print("\n── Running Model 1 Run #4 (MR v2 + DH v2 + BS v2) ─────────────────────")
print("  Allocation: $33.33/lot × 1 slot × 3 streams = $99.99\n")

model_presets = [
    ("Primary v2",     presets["Primary v2"]["start"],     presets["Primary v2"]["end"]),
    ("Full History",   presets["Full History"]["start"],   presets["Full History"]["end"]),
    ("Recent",         presets["Recent"]["start"],         presets["Recent"]["end"]),
    ("2026 YTD",       presets["2026 YTD"]["start"],       presets["2026 YTD"]["end"]),
    ("Primary Window", presets["Primary Window"]["start"], presets["Primary Window"]["end"]),
]

for preset_name, start, end in model_presets:
    run_model(allocations=ALLOCATION, start=start, end=end, model_id=1)
    with open(MODEL_LAST_RUN_PATH, "rb") as f:
        payload = pickle.load(f)
    cm  = payload["combined_metrics"]
    ann = cm.get("annualized_return_pct") or 0
    tr  = cm.get("total_trades", 0)
    dd  = cm.get("max_drawdown_pct") or 0

    save_model_test(
        payload=payload,
        preset_id=presets[preset_name]["preset_id"],
        notes="Run #4 — MR v2 + DH v2 + BS v2 | equal $33.33 allocation",
    )
    print(f"  {preset_name:<18} {ann:>+7.1f}%  {tr:>3} trades  DD {dd:.1f}%  ✓ saved")

print("\n  All done — open Model Tester to review Run #4.")
