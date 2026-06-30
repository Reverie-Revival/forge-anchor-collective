"""
DH v2 exploration — Primary v2 (2022-present).
Goal: complement MR v2 (bull trend follower). DH should fire in fear/bear regimes
where MR is sitting out.

Baseline (v1 config): +0.1% ann, 29 trades, 38% WR, -32.1% DD, PF 1.01

Key levers:
  - Timeframe: 1h (current) vs 4h — less noise, wider candles
  - Trailing stop: 7.5% (current) — likely too tight for bear-market volatility
  - F&G max: 20 (current) — try relaxing slightly
  - min_drop_pct: 25% (current) — how deep the dip must be
  - min_hold: 48 candles (current) — force holding through initial chop

Run: python -m src.backtester.explore_dh_v2
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

BASE_1H = {
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
        "min_hold_candles": 48,  # 2 days on 1h
    },
}

BASE_4H = {
    **copy.deepcopy(BASE_1H),
    "primary_timeframe": "4h",
    "position": {
        **BASE_1H["position"],
        "min_hold_candles": 12,  # 2 days on 4h
    },
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
        elif k == "rsi_thresh":
            params["core_params"]["rsi_threshold"] = v
    return params

def run_one(params, window="Primary v2"):
    start, end = windows[window]
    result = run_backtest(params, start=start, end=end, slot_count=1,
                          slot_mode="single", stream_name="DH", lot_size_usd=10.0)
    return compute_metrics(result["trades"], 10.0, result["start"], result["end"])

# ── Grid 1: Timeframe × Trailing Stop ────────────────────────────────────────
# 1h vs 4h × 7.5/10/12.5/15% — the two biggest levers
grid1 = [
    ("1h + trail 7.5% (baseline)",   p(BASE_1H)),
    ("1h + trail 10%",               p(BASE_1H, trail=10.0)),
    ("1h + trail 12.5%",             p(BASE_1H, trail=12.5)),
    ("1h + trail 15%",               p(BASE_1H, trail=15.0)),
    ("4h + trail 7.5%",              p(BASE_4H)),
    ("4h + trail 10%",               p(BASE_4H, trail=10.0)),
    ("4h + trail 12.5%",             p(BASE_4H, trail=12.5)),
    ("4h + trail 15%",               p(BASE_4H, trail=15.0)),
]

# ── Grid 2: F&G threshold × Min drop (on both TFs) ───────────────────────────
grid2 = [
    ("1h + F&G 15 + drop 25%",       p(BASE_1H, fg_max=15, trail=10.0)),
    ("1h + F&G 25 + drop 25%",       p(BASE_1H, fg_max=25, trail=10.0)),
    ("1h + F&G 30 + drop 25%",       p(BASE_1H, fg_max=30, trail=10.0)),
    ("1h + F&G 20 + drop 20%",       p(BASE_1H, fg_max=20, trail=10.0, drop=20.0)),
    ("1h + F&G 20 + drop 30%",       p(BASE_1H, fg_max=20, trail=10.0, drop=30.0)),
    ("1h + F&G 25 + drop 20%",       p(BASE_1H, fg_max=25, trail=10.0, drop=20.0)),
    ("4h + F&G 15 + drop 25%",       p(BASE_4H, fg_max=15, trail=10.0)),
    ("4h + F&G 25 + drop 25%",       p(BASE_4H, fg_max=25, trail=10.0)),
    ("4h + F&G 30 + drop 25%",       p(BASE_4H, fg_max=30, trail=10.0)),
    ("4h + F&G 20 + drop 20%",       p(BASE_4H, fg_max=20, trail=10.0, drop=20.0)),
    ("4h + F&G 20 + drop 30%",       p(BASE_4H, fg_max=20, trail=10.0, drop=30.0)),
    ("4h + F&G 25 + drop 20%",       p(BASE_4H, fg_max=25, trail=10.0, drop=20.0)),
]

# ── Grid 3: Min hold — force surviving through bear chop ─────────────────────
grid3 = [
    ("1h + hold 24h + trail 10%",    p(BASE_1H, hold=24,  trail=10.0)),
    ("1h + hold 72h + trail 10%",    p(BASE_1H, hold=72,  trail=10.0)),
    ("1h + hold 96h + trail 10%",    p(BASE_1H, hold=96,  trail=10.0)),
    ("1h + hold 120h + trail 10%",   p(BASE_1H, hold=120, trail=10.0)),
    ("4h + hold 3d (18c) + trail 10%",  p(BASE_4H, hold=18, trail=10.0)),
    ("4h + hold 4d (24c) + trail 10%",  p(BASE_4H, hold=24, trail=10.0)),
    ("4h + hold 5d (30c) + trail 10%",  p(BASE_4H, hold=30, trail=10.0)),
    ("4h + hold 7d (42c) + trail 10%",  p(BASE_4H, hold=42, trail=10.0)),
]

# ── Grid 4: Best combos — wider search ───────────────────────────────────────
grid4 = [
    ("4h + F&G 25 + drop 20% + 12%",  p(BASE_4H, fg_max=25, drop=20.0, trail=12.5)),
    ("4h + F&G 30 + drop 20% + 12%",  p(BASE_4H, fg_max=30, drop=20.0, trail=12.5)),
    ("4h + F&G 25 + drop 20% + 15%",  p(BASE_4H, fg_max=25, drop=20.0, trail=15.0)),
    ("4h + F&G 30 + drop 20% + 15%",  p(BASE_4H, fg_max=30, drop=20.0, trail=15.0)),
    ("4h + F&G 25 + hold 24c + 12%",  p(BASE_4H, fg_max=25, hold=24,   trail=12.5)),
    ("4h + F&G 25 + hold 24c + 15%",  p(BASE_4H, fg_max=25, hold=24,   trail=15.0)),
    ("4h + F&G 30 + hold 24c + 12%",  p(BASE_4H, fg_max=30, hold=24,   trail=12.5)),
    ("4h + F&G 30 + hold 24c + 15%",  p(BASE_4H, fg_max=30, hold=24,   trail=15.0)),
    ("1h + F&G 25 + hold 96h + 12%",  p(BASE_1H, fg_max=25, hold=96,   trail=12.5)),
    ("1h + F&G 25 + hold 96h + 15%",  p(BASE_1H, fg_max=25, hold=96,   trail=15.0)),
    ("1h + F&G 30 + drop 20% + 12%",  p(BASE_1H, fg_max=30, drop=20.0, trail=12.5)),
    ("1h + F&G 30 + drop 20% + 15%",  p(BASE_1H, fg_max=30, drop=20.0, trail=15.0)),
]

all_experiments = [
    ("── Grid 1: Timeframe × Trailing Stop ──", grid1),
    ("── Grid 2: F&G Threshold × Min Drop ───", grid2),
    ("── Grid 3: Min Hold Duration ───────────", grid3),
    ("── Grid 4: Best Combo Search ───────────", grid4),
]

print(f"\n{'DH v2 — Primary v2 (2022-present) Exploration':^76}")
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
        flag = " ◄" if ann >= 8 else ""
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
