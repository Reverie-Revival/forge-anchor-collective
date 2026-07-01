"""
One-shot script: save all BS v2 preset runs, lock stream_id=3, run Model Run #4.

Run: python -m src.backtester.lock_bs_v2_and_run_model
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv; load_dotenv()

from sqlalchemy import text
from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold
from src.backtester.model_runner import run_model
from src.app.db import get_engine, save_stream_test, save_model_test

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

LOT_SIZE   = 10.0
SLOT_COUNT = 1
SLOT_MODE  = "single"
STREAM_NAME = "Breakout Scout v2"

ALLOCATION = {
    "Momentum Rider v2": {"lot_size_usd": 33.33, "slot_count": 1, "slot_mode": "single"},
    "Dip Hunter v2":     {"lot_size_usd": 33.33, "slot_count": 1, "slot_mode": "single"},
    "Breakout Scout v2": {"lot_size_usd": 33.33, "slot_count": 1, "slot_mode": "single"},
}

engine = get_engine()

# ── 1. Load presets ───────────────────────────────────────────────────────────
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT preset_id, name, start_date, end_date FROM timeframe_presets WHERE is_active=TRUE"
    )).fetchall()
presets = {r.name: {"preset_id": r.preset_id, "start": str(r.start_date), "end": str(r.end_date) if r.end_date else None}
           for r in rows}

# ── 2. Save all preset runs for BS v2 ────────────────────────────────────────
print("\n── Saving BS v2 stream test runs ────────────────────────────────────────")
primary_v2_test_id = None

for preset_name in ["Primary v2", "Full History", "Recent", "2026 YTD", "Primary Window"]:
    p = presets[preset_name]
    result = run_backtest(BS_V2_PARAMS, start=p["start"], end=p["end"],
                          slot_count=SLOT_COUNT, slot_mode=SLOT_MODE,
                          stream_name=STREAM_NAME, lot_size_usd=LOT_SIZE)
    trades  = result["trades"]
    initial = LOT_SIZE * SLOT_COUNT
    metrics = compute_metrics(trades, initial, result["start"], result["end"])
    ending  = initial + (trades["pnl"].sum() if not trades.empty else 0)
    bh      = btc_buy_and_hold(result["df"], initial)

    payload = {
        "stream_name": STREAM_NAME, "params": BS_V2_PARAMS, "result": result,
        "trades": trades, "df": result["df"], "metrics": metrics, "bh": bh,
        "initial_capital": initial, "ending_balance": ending,
        "slot_count": SLOT_COUNT, "slot_mode": SLOT_MODE, "lot_size_usd": LOT_SIZE,
    }

    test_id, run_num = save_stream_test(
        stream_name=STREAM_NAME, params=BS_V2_PARAMS, result=result,
        metrics=metrics, initial_capital=initial, ending_balance=ending,
        payload=payload, preset_id=p["preset_id"],
        notes="BS v2 locked config — lookback 24h, SMA 200 above, F&G>=55, 10% trail",
    )

    ann = metrics.get("annualized_return_pct") or 0
    tr  = metrics.get("total_trades", 0)
    dd  = metrics.get("max_drawdown_pct") or 0
    pf  = metrics.get("profit_factor") or 0
    print(f"  [{test_id}] {preset_name:<18} {ann:>+7.1f}%  {tr:>3} trades  DD {dd:.1f}%  PF {pf:.2f}  (run #{run_num})")

    if preset_name == "Primary v2":
        primary_v2_test_id = test_id

# ── 3. Lock stream_id=3 as Breakout Scout v2 ─────────────────────────────────
print(f"\n── Locking stream_id=3 → Breakout Scout v2 (locked_test_id={primary_v2_test_id}) ──")
with engine.connect() as conn:
    conn.execute(text("""
        UPDATE backtest.streams SET
            stream_name    = 'Breakout Scout',
            stream_version = 'v2',
            parameters     = CAST(:params AS jsonb),
            slot_count     = :slot_count,
            slot_mode      = :slot_mode,
            locked_test_id = :test_id,
            locked_at      = NOW()
        WHERE stream_id = 3
    """), {
        "params":     json.dumps(BS_V2_PARAMS),
        "slot_count": SLOT_COUNT,
        "slot_mode":  SLOT_MODE,
        "test_id":    primary_v2_test_id,
    })
    conn.commit()
print("  Done — stream_id=3 locked.")

# ── 4. Run Model #4 across all presets ───────────────────────────────────────
print("\n── Running Model 1 Run #4 (MR v2 + DH v2 + BS v2) ─────────────────────")
print(f"  Allocation: $33.33/lot × 1 slot × 3 streams = $99.99\n")

model_presets = [
    ("Primary v2",     presets["Primary v2"]["start"],     presets["Primary v2"]["end"]),
    ("Full History",   presets["Full History"]["start"],   presets["Full History"]["end"]),
    ("Recent",         presets["Recent"]["start"],         presets["Recent"]["end"]),
    ("2026 YTD",       presets["2026 YTD"]["start"],       presets["2026 YTD"]["end"]),
    ("Primary Window", presets["Primary Window"]["start"], presets["Primary Window"]["end"]),
]

for preset_name, start, end in model_presets:
    payload = run_model(allocations=ALLOCATION, start=start, end=end, model_id=1)
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
