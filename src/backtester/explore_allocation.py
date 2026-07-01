"""
Model 1 allocation exploration — MR v2 + DH v2 + BS v2.

Run #4 baseline: equal $33.33 each = $99.99 total.
Question: does weighting MR heavier (strongest stream) improve the model?
Min lot size: $10. Each stream runs 1 slot.

Run: python -m src.backtester.explore_allocation
"""
import pickle, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv; load_dotenv()

from sqlalchemy import text
from src.backtester.model_runner import run_model, MODEL_LAST_RUN_PATH
from src.app.db import get_engine

engine = get_engine()
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT name, start_date, end_date FROM timeframe_presets "
        "WHERE name IN ('Primary v2','Full History','Recent','2026 YTD') AND is_active=TRUE"
    )).fetchall()
windows = {r[0]: (str(r[1]), str(r[2]) if r[2] else None) for r in rows}

def alloc(mr, bs, dh):
    return {
        "Momentum Rider v2": {"lot_size_usd": mr,  "slot_count": 1, "slot_mode": "single"},
        "Breakout Scout v2": {"lot_size_usd": bs,  "slot_count": 1, "slot_mode": "single"},
        "Dip Hunter v2":     {"lot_size_usd": dh,  "slot_count": 1, "slot_mode": "single"},
    }

def run_one(allocation, window="Primary v2"):
    start, end = windows[window]
    run_model(allocations=allocation, start=start, end=end, model_id=1)
    with open(MODEL_LAST_RUN_PATH, "rb") as f:
        payload = pickle.load(f)
    cm = payload["combined_metrics"]
    return {
        "ann": cm.get("annualized_return_pct") or 0,
        "tr":  cm.get("total_trades", 0),
        "dd":  cm.get("max_drawdown_pct") or 0,
        "pf":  cm.get("profit_factor") or 0,
        "cap": payload["total_capital"],
    }

# MR / BS / DH — label, allocation
experiments = [
    # label                          MR     BS     DH     total
    ("Equal $33 (Run #4 baseline)",  33.33, 33.33, 33.33),  # $99.99
    ("MR $40 / BS $35 / DH $25",    40.00, 35.00, 25.00),  # $100
    ("MR $45 / BS $35 / DH $20",    45.00, 35.00, 20.00),  # $100
    ("MR $50 / BS $30 / DH $20",    50.00, 30.00, 20.00),  # $100
    ("MR $50 / BS $40 / DH $10",    50.00, 40.00, 10.00),  # $100
    ("MR $45 / BS $45 / DH $10",    45.00, 45.00, 10.00),  # $100
    ("MR $40 / BS $40 / DH $20",    40.00, 40.00, 20.00),  # $100
    ("MR $55 / BS $30 / DH $15",    55.00, 30.00, 15.00),  # $100
    ("MR $60 / BS $25 / DH $15",    60.00, 25.00, 15.00),  # $100
    ("MR $60 / BS $30 / DH $10",    60.00, 30.00, 10.00),  # $100
]

print(f"\n{'Model 1 Allocation Exploration — Primary v2 (2022-present)':^80}")
print(f"{'Label':<38} {'Cap':>5} {'PV2%':>7} {'Tr':>4} {'DD%':>7} {'PF':>6}")
print("-" * 80)

all_results = []
for label, mr, bs, dh in experiments:
    a = alloc(mr, bs, dh)
    m = run_one(a)
    all_results.append((label, a, m["ann"], m["tr"], m["dd"], m["pf"], m["cap"]))
    flag = " ◄" if m["ann"] >= 17 else ""
    print(f"{label:<38} ${m['cap']:>4.0f}  {m['ann']:>+7.1f}% {m['tr']:>4} {m['dd']:>6.1f}% {m['pf']:>6.2f}{flag}")

print(f"\n── Top 5 by PV2 — cross-checked on Full History + Recent + 2026 YTD ────────")
top5 = sorted(all_results, key=lambda x: x[2], reverse=True)[:5]
print(f"{'Label':<38} {'PV2':>7} {'FH':>7} {'Recent':>8} {'YTD':>7} {'FH_DD':>7}")
print("-" * 82)
for label, a, ann_pv2, tr, dd, pf, cap in top5:
    mfh  = run_one(a, "Full History")
    mr   = run_one(a, "Recent")
    mytd = run_one(a, "2026 YTD")
    print(f"{label:<38} {ann_pv2:>+7.1f}% {mfh['ann']:>+7.1f}% {mr['ann']:>+8.1f}% {mytd['ann']:>+7.1f}% {mfh['dd']:>7.1f}%")
