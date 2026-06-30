"""
MR round 2 — focused combinations on Primary v2.
Targeting the EMA 30/x family + stop/filter combinations we haven't tried yet.
Run: python -m src.backtester.explore_mr_v2b
"""
import copy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics
from src.app.db import get_engine

engine = get_engine()
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT name, start_date, end_date FROM timeframe_presets "
        "WHERE name IN ('Primary v2','Full History','Recent') AND is_active=TRUE"
    )).fetchall()
windows = {r[0]: (str(r[1]), str(r[2]) if r[2] else None) for r in rows}

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
        "trailing_stop_pct": 5.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

def p(**kw):
    params = copy.deepcopy(BASE)
    for k, v in kw.items():
        if k == "trail":
            params["position"]["trailing_stop_pct"] = v
        elif k == "rsi_min":
            params["filters"]["rsi"]["min"] = v
        elif k == "ema":
            params["core_params"]["ema_short"] = v[0]
            params["core_params"]["ema_long"]  = v[1]
        elif k == "min_hold":
            params["position"]["min_hold_candles"] = v
        elif k == "fg_min":
            params["sentiment"]["fear_greed"]["min"] = v
        elif k == "partial":
            params["position"]["partial_exit"] = {"at_gain_pct": v[0], "exit_pct": v[1]}
    return params

def run_one(params, window="Primary v2"):
    start, end = windows[window]
    result = run_backtest(params, start=start, end=end, slot_count=1,
                          slot_mode="single", stream_name="MR", lot_size_usd=10.0)
    return compute_metrics(result["trades"], 10.0, result["start"], result["end"])

experiments = [
    # ── EMA 30/90 with stops we haven't tested ────────────────────────────────
    ("EMA 30/90 + trail 7%",            p(ema=(30, 90), trail=7.0)),
    ("EMA 30/90 + trail 8%",            p(ema=(30, 90), trail=8.0)),
    ("EMA 30/90 + RSI 55 + trail 7%",   p(ema=(30, 90), rsi_min=55, trail=7.0)),
    ("EMA 30/90 + RSI 60 + trail 7%",   p(ema=(30, 90), rsi_min=60, trail=7.0)),

    # ── EMA 30/60 variants ────────────────────────────────────────────────────
    ("EMA 30/60 + trail 8%",            p(ema=(30, 60), trail=8.0)),
    ("EMA 30/60 + trail 9%",            p(ema=(30, 60), trail=9.0)),
    ("EMA 30/60 + RSI 65 + trail 7%",   p(ema=(30, 60), rsi_min=65, trail=7.0)),
    ("EMA 30/60 + min24h + trail 7%",   p(ema=(30, 60), min_hold=6, trail=7.0)),
    ("EMA 30/60 + F&G 40 + trail 7%",   p(ema=(30, 60), fg_min=40, trail=7.0)),

    # ── Wider EMA spreads ─────────────────────────────────────────────────────
    ("EMA 20/80",                        p(ema=(20, 80))),
    ("EMA 20/80 + trail 7%",             p(ema=(20, 80), trail=7.0)),
    ("EMA 20/80 + RSI 60 + trail 7%",    p(ema=(20, 80), rsi_min=60, trail=7.0)),
    ("EMA 40/100",                       p(ema=(40, 100))),
    ("EMA 40/100 + trail 7%",            p(ema=(40, 100), trail=7.0)),
    ("EMA 40/80",                        p(ema=(40, 80))),
    ("EMA 40/80 + trail 7%",             p(ema=(40, 80), trail=7.0)),

    # ── Partial exit on top of best candidates ────────────────────────────────
    ("EMA 30/90 + partial 8%→50% + 7%", p(ema=(30, 90), partial=(8.0, 50), trail=7.0)),
    ("EMA 30/60 + partial 8%→50% + 7%", p(ema=(30, 60), partial=(8.0, 50), trail=7.0)),

    # ── RSI 65 combos — only 9 trades on PV2, try loosening EMA ──────────────
    ("EMA 30/60 + RSI 65",              p(ema=(30, 60), rsi_min=65)),
    ("EMA 30/90 + RSI 65",              p(ema=(30, 90), rsi_min=65)),
    ("EMA 30/90 + RSI 65 + trail 7%",   p(ema=(30, 90), rsi_min=65, trail=7.0)),

    # ── Min hold + EMA combos ─────────────────────────────────────────────────
    ("EMA 30/90 + min24h + trail 7%",   p(ema=(30, 90), min_hold=6, trail=7.0)),
    ("EMA 30/90 + min48h + trail 7%",   p(ema=(30, 90), min_hold=12, trail=7.0)),
]

print(f"\n{'Primary v2 (2022-present) — Round 2':^76}")
print(f"{'Label':<38} {'PV2%':>7} {'Tr':>4} {'WR%':>5} {'DD%':>7} {'PF':>6}")
print("-" * 74)

results = []
for label, params in experiments:
    m   = run_one(params)
    ann = m.get("annualized_return_pct") or 0
    tr  = m.get("total_trades", 0)
    wr  = (m.get("win_rate") or 0) * 100
    dd  = m.get("max_drawdown_pct") or 0
    pf  = m.get("profit_factor") or 0
    results.append((label, params, ann, tr, wr, dd, pf))
    flag = " ◄" if ann >= 12 else ""
    print(f"{label:<38} {ann:>+7.1f}% {tr:>4} {wr:>4.0f}% {dd:>6.1f}% {pf:>6.2f}{flag}")

print(f"\n── Top 5 by PV2 — cross-checked on Full History + Recent ──")
top5 = sorted(results, key=lambda x: x[2], reverse=True)[:5]
print(f"{'Label':<38} {'PV2':>7} {'FH':>7} {'Recent':>8} {'FH_tr':>6} {'FH_DD':>7}")
print("-" * 80)
for label, params, ann_pv2, tr, wr, dd, pf in top5:
    mfh = run_one(params, "Full History")
    mr  = run_one(params, "Recent")
    print(f"{label:<38} {ann_pv2:>+7.1f}% {mfh.get('annualized_return_pct') or 0:>+7.1f}% "
          f"{mr.get('annualized_return_pct') or 0:>+8.1f}% "
          f"{mfh.get('total_trades',0):>6} {mfh.get('max_drawdown_pct') or 0:>7.1f}%")
