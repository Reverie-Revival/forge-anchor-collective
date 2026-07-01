"""
BS v2 Round 2 — Refining the best config + scale_up slot test.

Round 1 finding: 10% trail + F&G 40 = +5.5% PV2, +18.4% FH, PF 1.43
Problem: Recent (2024+) is -6.8%. Need to protect the niche without copying MR/DH.

BS identity: volatility expansion play — market coils (ATR low, Bollinger squeeze),
sentiment neutral-to-greedy, then explodes with a strong breakout candle.
Distinct from MR (sustained trend) and DH (panic recovery).

Angles:
  A) max_hold — cut stale trades before losses compound (like DH fix)
  B) SMA 200 above — breakouts only in macro uptrend; gives BS a bull-regime identity
     distinct from DH's bear-regime role
  C) Lookback 24h combos — Round 1 found 24h interesting; cross with best stop/F&G
  D) scale_up 2 slots — pyramid into confirmed momentum; natural fit for breakout continuation

Run: python -m src.backtester.explore_bs_v2b
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

# Best from Round 1: 10% trail + F&G 40 (+5.5% PV2, +18.4% FH, PF 1.43)
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
    "sentiment": {"fear_greed": {"min": 40}},
    "position": {
        "trailing_stop_pct": 10.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

def p(base, **kw):
    params = copy.deepcopy(base)
    for k, v in kw.items():
        if k == "trail":
            params["position"]["trailing_stop_pct"] = v
        elif k == "fg_min":
            params["sentiment"]["fear_greed"]["min"] = v
        elif k == "max_hold":
            params["position"]["max_hold_candles"] = v
        elif k == "sma_above":
            params["filters"]["trend_context"] = {"sma_period": v, "require": "above"}
        elif k == "lookback":
            params["core_params"]["breakout_lookback"] = v
        elif k == "squeeze_max":
            params["filters"]["bollinger"]["squeeze"]["max_bandwidth_pct"] = v
    return params

def run_one(params, window="Primary v2", slots=1, slot_mode="single"):
    start, end = windows[window]
    lot = 10.0
    result = run_backtest(params, start=start, end=end, slot_count=slots,
                          slot_mode=slot_mode, stream_name="BS", lot_size_usd=lot)
    return compute_metrics(result["trades"], lot * slots, result["start"], result["end"])

# ── Grid A: max_hold — cut stale trades before losses compound ────────────────
# 1h: 5d=120c, 7d=168c, 10d=240c, 14d=336c
gridA = [
    ("R1 best (no max_hold)",                   p(BASE)),
    ("max_hold 5d (120c)",                      p(BASE, max_hold=120)),
    ("max_hold 7d (168c)",                      p(BASE, max_hold=168)),
    ("max_hold 10d (240c)",                     p(BASE, max_hold=240)),
    ("max_hold 14d (336c)",                     p(BASE, max_hold=336)),
    ("F&G 45 + max_hold 7d",                    p(BASE, fg_min=45, max_hold=168)),
    ("F&G 45 + max_hold 10d",                   p(BASE, fg_min=45, max_hold=240)),
    ("F&G 50 + max_hold 7d",                    p(BASE, fg_min=50, max_hold=168)),
    ("F&G 50 + max_hold 10d",                   p(BASE, fg_min=50, max_hold=240)),
]

# ── Grid B: SMA 200 above — breakouts only in macro uptrend ──────────────────
# Gives BS a distinct bull-regime identity (complements DH which fires in bear)
gridB = [
    ("SMA 200 above",                           p(BASE, sma_above=200)),
    ("SMA 200 above + F&G 45",                  p(BASE, sma_above=200, fg_min=45)),
    ("SMA 200 above + F&G 50",                  p(BASE, sma_above=200, fg_min=50)),
    ("SMA 200 above + max_hold 7d",             p(BASE, sma_above=200, max_hold=168)),
    ("SMA 200 above + max_hold 10d",            p(BASE, sma_above=200, max_hold=240)),
    ("SMA 200 above + F&G 45 + max_hold 7d",   p(BASE, sma_above=200, fg_min=45, max_hold=168)),
    ("SMA 200 above + F&G 50 + max_hold 7d",   p(BASE, sma_above=200, fg_min=50, max_hold=168)),
    ("SMA 100 above",                           p(BASE, sma_above=100)),
    ("SMA 100 above + F&G 45",                  p(BASE, sma_above=100, fg_min=45)),
    ("SMA 100 above + max_hold 7d",             p(BASE, sma_above=100, max_hold=168)),
]

# ── Grid C: Lookback 24h combos — more trades, test quality with best filters ─
gridC = [
    ("lookback 24h (baseline)",                 p(BASE, lookback=24)),
    ("lookback 24h + F&G 45",                   p(BASE, lookback=24, fg_min=45)),
    ("lookback 24h + F&G 50",                   p(BASE, lookback=24, fg_min=50)),
    ("lookback 24h + max_hold 7d",              p(BASE, lookback=24, max_hold=168)),
    ("lookback 24h + SMA 200 above",            p(BASE, lookback=24, sma_above=200)),
    ("lookback 24h + SMA 200 + F&G 45",        p(BASE, lookback=24, sma_above=200, fg_min=45)),
    ("lookback 24h + SMA 200 + max_hold 7d",   p(BASE, lookback=24, sma_above=200, max_hold=168)),
    ("lookback 24h + trail 7.5%",              p(BASE, lookback=24, trail=7.5)),
    ("lookback 24h + trail 12%",               p(BASE, lookback=24, trail=12.0)),
]

# ── Grid D: scale_up 2 slots — pyramid into confirmed breakout momentum ───────
# Tests the same best configs with slot_count=2, slot_mode=scale_up
# Initial capital $20 (2 × $10 lots)
gridD_configs = [
    ("2-slot | R1 best",                        p(BASE)),
    ("2-slot | max_hold 7d",                    p(BASE, max_hold=168)),
    ("2-slot | max_hold 10d",                   p(BASE, max_hold=240)),
    ("2-slot | SMA 200 above",                  p(BASE, sma_above=200)),
    ("2-slot | SMA 200 + max_hold 7d",          p(BASE, sma_above=200, max_hold=168)),
    ("2-slot | F&G 45 + max_hold 7d",           p(BASE, fg_min=45, max_hold=168)),
    ("2-slot | lookback 24h",                   p(BASE, lookback=24)),
    ("2-slot | lookback 24h + SMA 200",         p(BASE, lookback=24, sma_above=200)),
]

all_experiments = [
    ("── Grid A: max_hold — cut stale trades ──────────────", gridA,    1, "single"),
    ("── Grid B: SMA 200 above — bull-regime identity ─────", gridB,    1, "single"),
    ("── Grid C: Lookback 24h combos ─────────────────────", gridC,    1, "single"),
    ("── Grid D: scale_up 2 slots ($20) ──────────────────", gridD_configs, 2, "scale_up"),
]

print(f"\n{'BS v2 Round 2 — Primary v2 (2022-present)':^80}")
print(f"{'Label':<46} {'PV2%':>7} {'Tr':>4} {'WR%':>5} {'DD%':>7} {'PF':>6}")

all_results = []
for section, experiments, slots, slot_mode in all_experiments:
    print(f"\n{section}")
    print("-" * 80)
    for label, params in experiments:
        m   = run_one(params, slots=slots, slot_mode=slot_mode)
        ann = m.get("annualized_return_pct") or 0
        tr  = m.get("total_trades", 0)
        wr  = (m.get("win_rate") or 0) * 100
        dd  = m.get("max_drawdown_pct") or 0
        pf  = m.get("profit_factor") or 0
        all_results.append((label, params, ann, tr, wr, dd, pf, slots, slot_mode))
        flag = " ◄" if ann >= 5 else ""
        print(f"{label:<46} {ann:>+7.1f}% {tr:>4} {wr:>4.0f}% {dd:>6.1f}% {pf:>6.2f}{flag}")

print(f"\n── Top 5 by PV2 — cross-checked on Full History + Recent ──────────────────")
top5 = sorted(all_results, key=lambda x: x[2], reverse=True)[:5]
print(f"{'Label':<46} {'PV2':>7} {'FH':>7} {'Recent':>8} {'FH_tr':>6} {'FH_DD':>7}")
print("-" * 88)
for label, params, ann_pv2, tr, wr, dd, pf, slots, slot_mode in top5:
    mfh = run_one(params, "Full History",  slots=slots, slot_mode=slot_mode)
    mr  = run_one(params, "Recent",        slots=slots, slot_mode=slot_mode)
    print(f"{label:<46} {ann_pv2:>+7.1f}% {mfh.get('annualized_return_pct') or 0:>+7.1f}% "
          f"{mr.get('annualized_return_pct') or 0:>+8.1f}% "
          f"{mfh.get('total_trades',0):>6} {mfh.get('max_drawdown_pct') or 0:>7.1f}%")
