"""
DH v2 Round 2 — unexplored levers after Round 1 came back flat.

Round 1 finding: 40 configs, best was +0.8% on PV2. Nothing significant.
Problem: RSI recovery through 30 catches "first bounce" in 2022 bear — often fails.

Round 2 angles:
  A) Confirmed recovery: rsi_threshold=30 (oversold prev) + rsi filter min=35 (strong current recovery)
  B) Max hold: force-exit stale/losing trades after N days — reduce DD
  C) SMA 200 below filter: DH only in confirmed bear markets (complement to MR perfectly)
  D) Higher RSI threshold: prev<35, curr>=35 — catch mid-oversold recoveries more broadly
  E) Combos of above

Run: python -m src.backtester.explore_dh_v2b
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

# Best base from round 1: 1h, 7.5% trail, F&G <=20, drop 25%, hold 48h
BASE = {
    "primary_timeframe": "1h",
    "core_signal": "rsi_recovery",
    "core_params": {
        "rsi_period": 14,
        "rsi_threshold": 30,
        "require_bullish_candle": True,
    },
    "filters": {
        "drawdown_from_high": {
            "min_drop_pct": 25.0,
            "lookback_days": 90,
        },
    },
    "sentiment": {"fear_greed": {"max": 20}},
    "position": {
        "trailing_stop_pct": 7.5,
        "entry_order_type": "limit",
        "entry_expiry_candles": 1,
        "min_hold_candles": 48,
    },
}

BASE_4H = {
    **copy.deepcopy(BASE),
    "primary_timeframe": "4h",
    "position": {**BASE["position"], "min_hold_candles": 12},
}

def p(base, **kw):
    params = copy.deepcopy(base)
    for k, v in kw.items():
        if k == "trail":
            params["position"]["trailing_stop_pct"] = v
        elif k == "fg_max":
            params["sentiment"]["fear_greed"]["max"] = v
        elif k == "drop":
            params["filters"]["drawdown_from_high"]["min_drop_pct"] = v
        elif k == "hold":
            params["position"]["min_hold_candles"] = v
        elif k == "max_hold":
            params["position"]["max_hold_candles"] = v
        elif k == "rsi_thresh":
            params["core_params"]["rsi_threshold"] = v
        elif k == "rsi_filter_min":
            params["filters"]["rsi"] = {"min": v}
        elif k == "sma_below":
            params["filters"]["trend_context"] = {"sma_period": v, "require": "below"}
        elif k == "sma_above":
            params["filters"]["trend_context"] = {"sma_period": v, "require": "above"}
        elif k == "no_bull_candle":
            params["core_params"]["require_bullish_candle"] = False
    return params

def run_one(params, window="Primary v2"):
    start, end = windows[window]
    result = run_backtest(params, start=start, end=end, slot_count=1,
                          slot_mode="single", stream_name="DH", lot_size_usd=10.0)
    return compute_metrics(result["trades"], 10.0, result["start"], result["end"])

# ── Grid A: Confirmed recovery — rsi filter stacked on top of signal ─────────
# prev RSI < 30 (oversold) AND curr RSI >= min_filter (confirmed recovery)
gridA = [
    ("1h baseline",                           p(BASE)),
    ("1h + rsi filter min=33",               p(BASE, rsi_filter_min=33)),
    ("1h + rsi filter min=35",               p(BASE, rsi_filter_min=35)),
    ("1h + rsi filter min=38",               p(BASE, rsi_filter_min=38)),
    ("1h + rsi filter min=40",               p(BASE, rsi_filter_min=40)),
    ("4h baseline",                           p(BASE_4H)),
    ("4h + rsi filter min=33",               p(BASE_4H, rsi_filter_min=33)),
    ("4h + rsi filter min=35",               p(BASE_4H, rsi_filter_min=35)),
    ("4h + rsi filter min=38",               p(BASE_4H, rsi_filter_min=38)),
    ("4h + rsi filter min=40",               p(BASE_4H, rsi_filter_min=40)),
]

# ── Grid B: Max hold — cut losing/stale trades early ─────────────────────────
# 1h: 7d=168c, 10d=240c, 14d=336c / 4h: 7d=42c, 10d=60c, 14d=84c
gridB = [
    ("1h + max_hold 7d",                     p(BASE, max_hold=168)),
    ("1h + max_hold 10d",                    p(BASE, max_hold=240)),
    ("1h + max_hold 14d",                    p(BASE, max_hold=336)),
    ("4h + max_hold 7d",                     p(BASE_4H, max_hold=42)),
    ("4h + max_hold 10d",                    p(BASE_4H, max_hold=60)),
    ("4h + max_hold 14d",                    p(BASE_4H, max_hold=84)),
]

# ── Grid C: SMA 200 regime filter ─────────────────────────────────────────────
# "below" = bear market only (perfect complement to MR)
# "above" = dip buying in bull market only
gridC = [
    ("1h + below SMA 200",                   p(BASE, sma_below=200)),
    ("1h + below SMA 200 + F&G 25",          p(BASE, sma_below=200, fg_max=25)),
    ("1h + below SMA 200 + drop 20%",        p(BASE, sma_below=200, drop=20.0)),
    ("1h + below SMA 200 + trail 10%",       p(BASE, sma_below=200, trail=10.0)),
    ("4h + below SMA 200",                   p(BASE_4H, sma_below=200)),
    ("4h + below SMA 200 + F&G 25",          p(BASE_4H, sma_below=200, fg_max=25)),
    ("4h + below SMA 200 + drop 20%",        p(BASE_4H, sma_below=200, drop=20.0)),
    ("4h + below SMA 200 + trail 10%",       p(BASE_4H, sma_below=200, trail=10.0)),
    ("1h + above SMA 200 + F&G 25",          p(BASE, sma_above=200, fg_max=25)),
    ("4h + above SMA 200 + F&G 25",          p(BASE_4H, sma_above=200, fg_max=25)),
]

# ── Grid D: Best combos — stack the winning levers ───────────────────────────
gridD = [
    ("1h + rsi35 + max10d",                  p(BASE, rsi_filter_min=35, max_hold=240)),
    ("1h + rsi35 + max10d + F&G 25",         p(BASE, rsi_filter_min=35, max_hold=240, fg_max=25)),
    ("1h + rsi35 + max10d + trail 10%",      p(BASE, rsi_filter_min=35, max_hold=240, trail=10.0)),
    ("1h + rsi35 + SMA below",               p(BASE, rsi_filter_min=35, sma_below=200)),
    ("1h + rsi35 + SMA below + F&G 25",      p(BASE, rsi_filter_min=35, sma_below=200, fg_max=25)),
    ("1h + rsi35 + SMA below + drop 20%",    p(BASE, rsi_filter_min=35, sma_below=200, drop=20.0)),
    ("4h + rsi35 + max10d",                  p(BASE_4H, rsi_filter_min=35, max_hold=60)),
    ("4h + rsi35 + max10d + F&G 25",         p(BASE_4H, rsi_filter_min=35, max_hold=60, fg_max=25)),
    ("4h + rsi35 + SMA below",               p(BASE_4H, rsi_filter_min=35, sma_below=200)),
    ("4h + rsi35 + SMA below + F&G 25",      p(BASE_4H, rsi_filter_min=35, sma_below=200, fg_max=25)),
    ("4h + rsi35 + SMA below + drop 20%",    p(BASE_4H, rsi_filter_min=35, sma_below=200, drop=20.0)),
    ("1h + rsi38 + SMA below + F&G 25",      p(BASE, rsi_filter_min=38, sma_below=200, fg_max=25)),
]

all_experiments = [
    ("── Grid A: Confirmed Recovery (RSI filter stacked) ──", gridA),
    ("── Grid B: Max Hold — cut stale trades ─────────────", gridB),
    ("── Grid C: SMA 200 Regime Filter ───────────────────", gridC),
    ("── Grid D: Best Combo Search ───────────────────────", gridD),
]

print(f"\n{'DH v2 Round 2 — Primary v2 (2022-present)':^76}")
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
