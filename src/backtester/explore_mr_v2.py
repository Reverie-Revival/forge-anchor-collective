"""
MR exploration focused on Primary v2 (2022-present).
Target: 17%+ annualized while keeping EMA crossover momentum identity.
Run: python -m src.backtester.explore_mr_v2
"""
import copy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text
load_dotenv()

from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics
from src.app.db import get_engine

engine = get_engine()
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT name, start_date, end_date FROM timeframe_presets "
        "WHERE name IN ('Primary v2', 'Full History') AND is_active=TRUE"
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
        elif k == "tf":
            params["primary_timeframe"] = v
        elif k == "min_hold":
            params["position"]["min_hold_candles"] = v
        elif k == "fg_min":
            params["sentiment"]["fear_greed"]["min"] = v
        elif k == "atr":
            params["filters"]["atr_regime"] = {
                "period": 14, "avg_period": 30, "max_pct_of_avg": v
            }
        elif k == "atr_min":
            # require ATR ABOVE avg × threshold (expanding volatility)
            params["filters"]["atr_regime"] = {
                "period": 14, "avg_period": 30,
                "max_pct_of_avg": 999,           # no ceiling
                "min_pct_of_avg": v,
            }
        elif k == "partial":
            params["position"]["partial_exit"] = {
                "at_gain_pct": v[0], "exit_pct": v[1]
            }
        elif k == "no_rsi":
            params["filters"].pop("rsi", None)
    return params


def run_one(params, window="Primary v2"):
    start, end = windows[window]
    result  = run_backtest(params, start=start, end=end, slot_count=1,
                           slot_mode="single", stream_name="MR", lot_size_usd=10.0)
    m = compute_metrics(result["trades"], 10.0, result["start"], result["end"])
    return m


experiments = [
    # ── baseline ──────────────────────────────────────────────────────────────
    ("baseline 4h/5%",              p()),

    # ── RSI filter — stricter momentum gate ───────────────────────────────────
    ("RSI 60",                      p(rsi_min=60)),
    ("RSI 65",                      p(rsi_min=65)),
    ("RSI 60 + trail 7%",           p(rsi_min=60, trail=7.0)),
    ("RSI 65 + trail 7%",           p(rsi_min=65, trail=7.0)),

    # ── slower EMA pairs — less noise ─────────────────────────────────────────
    ("EMA 30/60",                   p(ema=(30, 60))),
    ("EMA 30/90",                   p(ema=(30, 90))),
    ("EMA 30/60 + RSI 60",          p(ema=(30, 60), rsi_min=60)),
    ("EMA 30/60 + trail 7%",        p(ema=(30, 60), trail=7.0)),
    ("EMA 30/90 + RSI 60",          p(ema=(30, 90), rsi_min=60)),

    # ── daily candles — cleanest signal ───────────────────────────────────────
    ("1d candles",                  p(tf="1d")),
    ("1d + trail 7%",               p(tf="1d", trail=7.0)),
    ("1d + trail 10%",              p(tf="1d", trail=10.0)),
    ("1d + RSI 60",                 p(tf="1d", rsi_min=60)),
    ("1d + RSI 60 + trail 7%",      p(tf="1d", rsi_min=60, trail=7.0)),
    ("1d + RSI 60 + trail 10%",     p(tf="1d", rsi_min=60, trail=10.0)),
    ("1d EMA 20/100",               p(tf="1d", ema=(20, 100))),
    ("1d EMA 20/100 + trail 7%",    p(tf="1d", ema=(20, 100), trail=7.0)),
    ("1d EMA 10/30",                p(tf="1d", ema=(10, 30))),

    # ── partial exit — lock profit on winners ─────────────────────────────────
    ("partial 5%→50% + trail 5%",   p(partial=(5.0, 50))),
    ("partial 8%→50% + trail 5%",   p(partial=(8.0, 50))),
    ("partial 5%→50% + trail 7%",   p(partial=(5.0, 50), trail=7.0)),
    ("1d + partial 8%→50%",         p(tf="1d", partial=(8.0, 50))),
    ("1d + partial 8%→50% trail 7%",p(tf="1d", partial=(8.0, 50), trail=7.0)),

    # ── min hold — let trades develop before stop fires ───────────────────────
    ("min_hold 24h (6c)",           p(min_hold=6)),
    ("min_hold 48h (12c)",          p(min_hold=12)),
    ("RSI 60 + min_hold 24h",       p(rsi_min=60, min_hold=6)),
    ("1d + min_hold 3d",            p(tf="1d", min_hold=3)),

    # ── F&G filter tighter ────────────────────────────────────────────────────
    ("F&G > 40",                    p(fg_min=40)),
    ("F&G > 50",                    p(fg_min=50)),
    ("1d + F&G > 40",               p(tf="1d", fg_min=40)),

    # ── best combo candidates ─────────────────────────────────────────────────
    ("1d + RSI 60 + F&G 40 + 7%",  p(tf="1d", rsi_min=60, fg_min=40, trail=7.0)),
    ("EMA30/60 + RSI60 + trail7%",  p(ema=(30, 60), rsi_min=60, trail=7.0)),
    ("4h RSI65 + min24h + trail7%", p(rsi_min=65, min_hold=6, trail=7.0)),
    ("1d EMA20/100 + RSI60 + 7%",  p(tf="1d", ema=(20, 100), rsi_min=60, trail=7.0)),
]

print(f"\n{'Primary v2 (2022-present)':^70}")
print(f"{'Label':<36} {'Ann%':>7} {'Trades':>7} {'WR%':>6} {'MaxDD%':>8} {'PF':>6}")
print("-" * 76)

results = []
for label, params in experiments:
    m    = run_one(params)
    ann  = m.get("annualized_return_pct") or 0
    tr   = m.get("total_trades", 0)
    wr   = (m.get("win_rate") or 0) * 100
    dd   = m.get("max_drawdown_pct") or 0
    pf   = m.get("profit_factor") or 0
    results.append((label, params, ann, tr, wr, dd, pf))
    flag = " ◄" if ann >= 17 else ""
    print(f"{label:<36} {ann:>+7.1f}% {tr:>7} {wr:>5.0f}% {dd:>7.1f}% {pf:>6.2f}{flag}")

print(f"\n── Hits 17%+ on Primary v2 ──────────────────")
hits = [(l, ann, tr, wr, dd, pf, params) for l, params, ann, tr, wr, dd, pf in results if ann >= 17]
if hits:
    for label, ann, tr, wr, dd, pf, _ in sorted(hits, key=lambda x: x[1], reverse=True):
        print(f"  {label:<36} {ann:+.1f}%  trades={tr}  DD={dd:.1f}%  PF={pf:.2f}")
else:
    print("  None hit 17% — showing top 5:")
    for label, _, ann, tr, wr, dd, pf in sorted(results, key=lambda x: x[2], reverse=True)[:5]:
        print(f"  {label:<36} {ann:+.1f}%  trades={tr}  DD={dd:.1f}%  PF={pf:.2f}")

# Cross-check top 5 against Full History
print(f"\n── Top 5 cross-checked on Full History (2018-present) ──")
top5 = sorted(results, key=lambda x: x[2], reverse=True)[:5]
for label, params, ann_pv2, tr, wr, dd, pf in top5:
    m2   = run_one(params, "Full History")
    ann2 = m2.get("annualized_return_pct") or 0
    tr2  = m2.get("total_trades", 0)
    dd2  = m2.get("max_drawdown_pct") or 0
    print(f"  {label:<36} PV2={ann_pv2:+.1f}%  FH={ann2:+.1f}%  FH_trades={tr2}  FH_DD={dd2:.1f}%")
