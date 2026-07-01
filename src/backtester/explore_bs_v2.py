"""
BS v2 Round 1 — Breakout Scout tuning for Primary v2 (2022-present).

Baseline finding: BS v1 on Primary v2 = -1.8%, 11 trades, 55% WR, PF 0.64
Problem: winners cut too early (5% trail), losers run to full stop.
WR is decent — fix the stop first, then widen trade volume if still thin.

Angles:
  A) Trailing stop width — 5% is too tight for breakout continuation
  B) Entry relaxation — Bollinger squeeze (6%→8/10%) + ATR max (90%→110/130%)
  C) F&G floor — 50 is strict; test 40/45 and no floor
  D) Breakout lookback — 48h (2d) baseline; try 24h and 72h
  E) Best combos — stack winning levers

Run: python -m src.backtester.explore_bs_v2
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
    "primary_timeframe": "1h",
    "core_signal": "range_breakout",
    "core_params": {
        "breakout_lookback": 48,
    },
    "filters": {
        "bollinger": {
            "period": 20,
            "std_dev": 2.0,
            "squeeze": {"max_bandwidth_pct": 6.0},
        },
        "atr_regime": {
            "period": 14,
            "avg_period": 30,
            "max_pct_of_avg": 90,
        },
        "breakout_candle": {
            "body_ratio_min": 0.4,
            "close_position_min": 0.6,
        },
    },
    "sentiment": {"fear_greed": {"min": 50}},
    "position": {
        "trailing_stop_pct": 5.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

def p(base, **kw):
    params = copy.deepcopy(base)
    for k, v in kw.items():
        if k == "trail":
            params["position"]["trailing_stop_pct"] = v
        elif k == "squeeze_max":
            params["filters"]["bollinger"]["squeeze"]["max_bandwidth_pct"] = v
        elif k == "no_squeeze":
            del params["filters"]["bollinger"]["squeeze"]
        elif k == "atr_max":
            params["filters"]["atr_regime"]["max_pct_of_avg"] = v
        elif k == "no_atr":
            del params["filters"]["atr_regime"]
        elif k == "fg_min":
            params["sentiment"]["fear_greed"]["min"] = v
        elif k == "no_fg":
            del params["sentiment"]
        elif k == "lookback":
            params["core_params"]["breakout_lookback"] = v
        elif k == "body_ratio":
            params["filters"]["breakout_candle"]["body_ratio_min"] = v
        elif k == "no_candle_filter":
            del params["filters"]["breakout_candle"]
    return params

def run_one(params, window="Primary v2"):
    start, end = windows[window]
    result = run_backtest(params, start=start, end=end, slot_count=1,
                          slot_mode="single", stream_name="BS", lot_size_usd=10.0)
    return compute_metrics(result["trades"], 10.0, result["start"], result["end"])

# ── Grid A: Trailing stop width — fix winners getting cut too early ───────────
gridA = [
    ("v1 baseline (5% trail)",                  p(BASE)),
    ("7.5% trail",                              p(BASE, trail=7.5)),
    ("10% trail",                               p(BASE, trail=10.0)),
    ("12% trail",                               p(BASE, trail=12.0)),
    ("15% trail",                               p(BASE, trail=15.0)),
]

# ── Grid B: Loosen entry — squeeze + ATR limiting trade count ─────────────────
gridB = [
    ("squeeze 8%",                              p(BASE, squeeze_max=8.0)),
    ("squeeze 10%",                             p(BASE, squeeze_max=10.0)),
    ("squeeze 12%",                             p(BASE, squeeze_max=12.0)),
    ("no squeeze filter",                       p(BASE, no_squeeze=True)),
    ("ATR max 110%",                            p(BASE, atr_max=110)),
    ("ATR max 130%",                            p(BASE, atr_max=130)),
    ("no ATR filter",                           p(BASE, no_atr=True)),
    ("squeeze 8% + ATR 110%",                   p(BASE, squeeze_max=8.0, atr_max=110)),
    ("squeeze 10% + ATR 130%",                  p(BASE, squeeze_max=10.0, atr_max=130)),
    ("no squeeze + no ATR",                     p(BASE, no_squeeze=True, no_atr=True)),
]

# ── Grid C: F&G floor relaxation — 50 strict in fear-heavy 2022+ regime ──────
gridC = [
    ("F&G min=45",                              p(BASE, fg_min=45)),
    ("F&G min=40",                              p(BASE, fg_min=40)),
    ("F&G min=35",                              p(BASE, fg_min=35)),
    ("no F&G filter",                           p(BASE, no_fg=True)),
]

# ── Grid D: Breakout lookback — does window size matter? ─────────────────────
gridD = [
    ("lookback 24h",                            p(BASE, lookback=24)),
    ("lookback 36h",                            p(BASE, lookback=36)),
    ("lookback 72h",                            p(BASE, lookback=72)),
    ("lookback 96h",                            p(BASE, lookback=96)),
]

# ── Grid E: Best combos — stack the most promising levers ────────────────────
# (populated after reviewing A–D results; pre-loaded with high-probability guesses)
gridE = [
    ("10% trail + squeeze 8%",                  p(BASE, trail=10.0, squeeze_max=8.0)),
    ("10% trail + squeeze 10%",                 p(BASE, trail=10.0, squeeze_max=10.0)),
    ("10% trail + ATR 110%",                    p(BASE, trail=10.0, atr_max=110)),
    ("10% trail + no ATR",                      p(BASE, trail=10.0, no_atr=True)),
    ("10% trail + F&G 40",                      p(BASE, trail=10.0, fg_min=40)),
    ("10% trail + F&G 45",                      p(BASE, trail=10.0, fg_min=45)),
    ("10% trail + squeeze 8% + F&G 45",         p(BASE, trail=10.0, squeeze_max=8.0, fg_min=45)),
    ("10% trail + squeeze 10% + F&G 40",        p(BASE, trail=10.0, squeeze_max=10.0, fg_min=40)),
    ("10% trail + no squeeze + F&G 45",         p(BASE, trail=10.0, no_squeeze=True, fg_min=45)),
    ("10% trail + no ATR + F&G 45",             p(BASE, trail=10.0, no_atr=True, fg_min=45)),
    ("10% trail + no squeeze + ATR 110%",       p(BASE, trail=10.0, no_squeeze=True, atr_max=110)),
    ("12% trail + squeeze 8% + F&G 45",         p(BASE, trail=12.0, squeeze_max=8.0, fg_min=45)),
    ("12% trail + no squeeze + F&G 40",         p(BASE, trail=12.0, no_squeeze=True, fg_min=40)),
    ("7.5% trail + squeeze 8% + F&G 45",        p(BASE, trail=7.5, squeeze_max=8.0, fg_min=45)),
]

all_experiments = [
    ("── Grid A: Trailing Stop Width ──────────────────────", gridA),
    ("── Grid B: Entry Relaxation (squeeze + ATR) ─────────", gridB),
    ("── Grid C: F&G Floor ────────────────────────────────", gridC),
    ("── Grid D: Breakout Lookback ────────────────────────", gridD),
    ("── Grid E: Best Combos ──────────────────────────────", gridE),
]

print(f"\n{'BS v2 Round 1 — Primary v2 (2022-present)':^76}")
print(f"{'Label':<44} {'PV2%':>7} {'Tr':>4} {'WR%':>5} {'DD%':>7} {'PF':>6}")

all_results = []
for section, experiments in all_experiments:
    print(f"\n{section}")
    print("-" * 76)
    for label, params in experiments:
        m   = run_one(params)
        ann = m.get("annualized_return_pct") or 0
        tr  = m.get("total_trades", 0)
        wr  = (m.get("win_rate") or 0) * 100
        dd  = m.get("max_drawdown_pct") or 0
        pf  = m.get("profit_factor") or 0
        all_results.append((label, params, ann, tr, wr, dd, pf))
        flag = " ◄" if ann >= 5 else ""
        print(f"{label:<44} {ann:>+7.1f}% {tr:>4} {wr:>4.0f}% {dd:>6.1f}% {pf:>6.2f}{flag}")

print(f"\n── Top 5 by PV2 — cross-checked on Full History + Recent ──────────────────")
top5 = sorted(all_results, key=lambda x: x[2], reverse=True)[:5]
print(f"{'Label':<44} {'PV2':>7} {'FH':>7} {'Recent':>8} {'FH_tr':>6} {'FH_DD':>7}")
print("-" * 84)
for label, params, ann_pv2, tr, wr, dd, pf in top5:
    mfh = run_one(params, "Full History")
    mr  = run_one(params, "Recent")
    print(f"{label:<44} {ann_pv2:>+7.1f}% {mfh.get('annualized_return_pct') or 0:>+7.1f}% "
          f"{mr.get('annualized_return_pct') or 0:>+8.1f}% "
          f"{mfh.get('total_trades',0):>6} {mfh.get('max_drawdown_pct') or 0:>7.1f}%")
