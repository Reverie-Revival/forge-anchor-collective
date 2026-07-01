"""
BS v2 Round 3 — Targeted refinement + scale_up on top R2 candidates.

R2 top candidates:
  A) lookback 24h + F&G 50:            PV2 +11.2%, FH +21.8%, Recent +11.4%, DD -39.6%
  B) lookback 24h + SMA 200 + F&G 45:  PV2 +10.3%, FH +21.8%, Recent  +8.7%, DD -30.6%

Questions:
  1) Can we add SMA 200 to candidate A and keep the returns while lowering DD?
  2) Does SMA 50/100 work better as a regime filter than 200?
  3) Does F&G 50 vs 45 vs 55 matter when SMA filter is present?
  4) Scale_up (2 slots) on the actual top R2 configs — we only tested R1 configs before

Run: python -m src.backtester.explore_bs_v2c
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
        "WHERE name IN ('Primary v2','Full History','Recent','2026 YTD') AND is_active=TRUE"
    )).fetchall()
windows = {r[0]: (str(r[1]), str(r[2]) if r[2] else None) for r in rows}

# Candidate A: best raw returns
CAND_A = {
    "primary_timeframe": "1h",
    "core_signal": "range_breakout",
    "core_params": {"breakout_lookback": 24},
    "filters": {
        "bollinger": {"period": 20, "std_dev": 2.0, "squeeze": {"max_bandwidth_pct": 6.0}},
        "atr_regime": {"period": 14, "avg_period": 30, "max_pct_of_avg": 90},
        "breakout_candle": {"body_ratio_min": 0.4, "close_position_min": 0.6},
    },
    "sentiment": {"fear_greed": {"min": 50}},
    "position": {
        "trailing_stop_pct": 10.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

# Candidate B: best DD + identity
CAND_B = {
    **copy.deepcopy(CAND_A),
    "filters": {
        **copy.deepcopy(CAND_A["filters"]),
        "trend_context": {"sma_period": 200, "require": "above"},
    },
    "sentiment": {"fear_greed": {"min": 45}},
}

def p(base, **kw):
    params = copy.deepcopy(base)
    for k, v in kw.items():
        if k == "trail":
            params["position"]["trailing_stop_pct"] = v
        elif k == "fg_min":
            params["sentiment"]["fear_greed"]["min"] = v
        elif k == "sma_above":
            params["filters"]["trend_context"] = {"sma_period": v, "require": "above"}
        elif k == "no_sma":
            params["filters"].pop("trend_context", None)
        elif k == "lookback":
            params["core_params"]["breakout_lookback"] = v
    return params

def run_one(params, window="Primary v2", slots=1, slot_mode="single"):
    start, end = windows[window]
    lot = 10.0
    result = run_backtest(params, start=start, end=end, slot_count=slots,
                          slot_mode=slot_mode, stream_name="BS", lot_size_usd=lot)
    return compute_metrics(result["trades"], lot * slots, result["start"], result["end"])

# ── Grid A: Add SMA filter to candidate A — can we tame the DD? ───────────────
gridA = [
    ("CandA baseline (no SMA)",                 p(CAND_A)),
    ("CandA + SMA 50 above",                    p(CAND_A, sma_above=50)),
    ("CandA + SMA 100 above",                   p(CAND_A, sma_above=100)),
    ("CandA + SMA 200 above",                   p(CAND_A, sma_above=200)),
    ("CandA + SMA 200 + F&G 45",                p(CAND_A, sma_above=200, fg_min=45)),
    ("CandA + SMA 200 + F&G 55",                p(CAND_A, sma_above=200, fg_min=55)),
    ("CandA + SMA 100 + F&G 45",                p(CAND_A, sma_above=100, fg_min=45)),
    ("CandA + SMA 100 + F&G 50",                p(CAND_A, sma_above=100, fg_min=50)),
    ("CandA + SMA 50 + F&G 50",                 p(CAND_A, sma_above=50, fg_min=50)),
]

# ── Grid B: Candidate B variations ────────────────────────────────────────────
gridB = [
    ("CandB baseline (SMA200 + F&G45)",         p(CAND_B)),
    ("CandB + F&G 50",                          p(CAND_B, fg_min=50)),
    ("CandB + F&G 55",                          p(CAND_B, fg_min=55)),
    ("CandB + F&G 40",                          p(CAND_B, fg_min=40)),
    ("CandB + trail 7.5%",                      p(CAND_B, trail=7.5)),
    ("CandB + trail 12%",                       p(CAND_B, trail=12.0)),
    ("CandB + SMA 100",                         p(CAND_B, sma_above=100, no_sma=False)),
]

# ── Grid C: scale_up 2 slots on top R2 candidates ────────────────────────────
gridC_configs = [
    ("2-slot | CandA (no SMA)",                 p(CAND_A)),
    ("2-slot | CandA + SMA 200",                p(CAND_A, sma_above=200)),
    ("2-slot | CandA + SMA 200 + F&G 45",       p(CAND_A, sma_above=200, fg_min=45)),
    ("2-slot | CandA + SMA 100",                p(CAND_A, sma_above=100)),
    ("2-slot | CandB (SMA200 + F&G45)",         p(CAND_B)),
    ("2-slot | CandB + F&G 50",                 p(CAND_B, fg_min=50)),
    ("2-slot | CandB + trail 7.5%",             p(CAND_B, trail=7.5)),
]

all_experiments = [
    ("── Grid A: SMA filter on Candidate A ────────────────", gridA,        1, "single"),
    ("── Grid B: Candidate B refinements ─────────────────", gridB,        1, "single"),
    ("── Grid C: scale_up 2 slots on top R2 configs ──────", gridC_configs, 2, "scale_up"),
]

print(f"\n{'BS v2 Round 3 — Primary v2 (2022-present)':^80}")
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
        flag = " ◄" if ann >= 8 else ""
        print(f"{label:<46} {ann:>+7.1f}% {tr:>4} {wr:>4.0f}% {dd:>6.1f}% {pf:>6.2f}{flag}")

print(f"\n── Top 5 by PV2 — cross-checked on Full History + Recent + 2026 YTD ────────")
top5 = sorted(all_results, key=lambda x: x[2], reverse=True)[:5]
print(f"{'Label':<46} {'PV2':>7} {'FH':>7} {'Recent':>8} {'YTD':>7} {'FH_DD':>7}")
print("-" * 90)
for label, params, ann_pv2, tr, wr, dd, pf, slots, slot_mode in top5:
    mfh  = run_one(params, "Full History", slots=slots, slot_mode=slot_mode)
    mr   = run_one(params, "Recent",       slots=slots, slot_mode=slot_mode)
    mytd = run_one(params, "2026 YTD",     slots=slots, slot_mode=slot_mode)
    print(f"{label:<46} {ann_pv2:>+7.1f}% {mfh.get('annualized_return_pct') or 0:>+7.1f}% "
          f"{mr.get('annualized_return_pct') or 0:>+8.1f}% "
          f"{mytd.get('annualized_return_pct') or 0:>+7.1f}% "
          f"{mfh.get('max_drawdown_pct') or 0:>7.1f}%")
