"""
The Gauntlet (4-part, 2026-08-05 revision) run against GS: Reflex v2
(stream_config_id=38) on the clean, cut-over main branch.

Part 1: single-year walk-forward (fixed-parameter config, no re-tuning per split)
Part 2: bootstrap distribution -- worst/mid/best case report
Part 3: real historical bear-market tests
Part 4: code review -- done separately during the cutover itself (line-by-line
        audit + independently hand-traced position); referenced, not repeated here.
"""
import sys
sys.path.insert(0, "/Users/reverierevival/Documents/forge-anchor-collective")
import random
import pandas as pd
from src.app.db import load_stream_configs
from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold

cfg = next(c for c in load_stream_configs(12) if c["version"] == "v2")
CAPITAL = 100.0

def run(start, end=None):
    return run_backtest(params=cfg["params"], start=start, end=end,
                         slot_count=cfg["slot_count"], slot_mode=cfg["slot_mode"],
                         stream_name="GS: Reflex", lot_size_usd=CAPITAL)

print("=" * 70)
print("PART 1 — Single-year walk-forward (2018-2026 YTD)")
print("=" * 70)
years_results = []
for year in range(2018, 2027):
    start = f"{year}-01-01"
    end = None if year == 2026 else f"{year}-12-31"
    result = run(start, end)
    m = compute_metrics(result["trades"], CAPITAL, result["start"], result["end"])
    ann = m["annualized_return_pct"]
    years_results.append((year, ann, m["total_trades"]))
    flag = "OK" if ann and ann > 0 else "*** NEGATIVE ***"
    print(f"  {year}: trades={m['total_trades']:>4}  ann={ann:>8.2f}%  {flag}")

n_positive = sum(1 for _, ann, _ in years_results if ann and ann > 0)
print(f"\n  {n_positive}/{len(years_results)} years positive.")

print()
print("=" * 70)
print("PART 2 — Bootstrap distribution: worst / mid / best case")
print("=" * 70)

full = run("2018-01-01", None)
trades = full["trades"].sort_values("exit_ts").reset_index(drop=True)
real_metrics = compute_metrics(trades, CAPITAL, full["start"], full["end"])
real_ann = real_metrics["annualized_return_pct"]
real_ending = CAPITAL + real_metrics["total_pnl"]

# Reconstruct each trade's growth factor relative to the compounding pool
# size AT THE TIME it happened (not a flat dollar reshuffle, not naive
# 100%-utilization) -- this is what "properly models partial capital
# deployment" means in practice.
pool = CAPITAL
factors = []
for pnl in trades["pnl"]:
    factor = 1 + (pnl / pool)
    factors.append(factor)
    pool += pnl

# sanity check: replaying factors in original order must reconstruct the real ending capital
reconstructed = CAPITAL
for f in factors:
    reconstructed *= f
assert abs(reconstructed - real_ending) < 0.01, f"reconstruction mismatch: {reconstructed} vs {real_ending}"
print(f"  Reconstruction check: {reconstructed:.2f} vs real {real_ending:.2f} -- OK\n")

random.seed(42)
N_RESAMPLES = 10000
n_trades = len(factors)
resampled_endings = []
for _ in range(N_RESAMPLES):
    capital = CAPITAL
    for _ in range(n_trades):
        capital *= random.choice(factors)
    resampled_endings.append(capital)

resampled_endings.sort()
years_span = (full["end"] - full["start"]).days / 365.25

def pct(p):
    idx = int(len(resampled_endings) * p / 100)
    idx = min(idx, len(resampled_endings) - 1)
    return resampled_endings[idx]

def ann_from_ending(ending):
    total_return = (ending - CAPITAL) / CAPITAL
    base = 1 + total_return
    return ((base ** (1 / years_span)) - 1) * 100 if base > 0 else None

p5, p50, p95 = pct(5), pct(50), pct(95)
n_losses = sum(1 for e in resampled_endings if e < CAPITAL)
real_rank = sum(1 for e in resampled_endings if e <= real_ending) / len(resampled_endings) * 100

print(f"  Worst case (5th pct):  ${p5:>10,.2f}   ({ann_from_ending(p5):>7.2f}% ann.)")
print(f"  Mid case   (median):   ${p50:>10,.2f}   ({ann_from_ending(p50):>7.2f}% ann.)")
print(f"  Best case  (95th pct): ${p95:>10,.2f}   ({ann_from_ending(p95):>7.2f}% ann.)")
print(f"\n  Real historical result: ${real_ending:,.2f} ({real_ann:.2f}% ann.) -- {real_rank:.1f}th percentile of the distribution")
print(f"  {n_losses}/{N_RESAMPLES} resamples ({n_losses/N_RESAMPLES*100:.2f}%) ended below the ${CAPITAL:.0f} starting capital")

print()
print("=" * 70)
print("PART 3 — Real historical bear-market tests")
print("=" * 70)
windows = [
    ("2018 crypto winter (full year)",        "2018-01-01", "2018-12-31"),
    ("2022 bear (full calendar year)",        "2022-01-01", "2022-12-31"),
    ("2021-2022 peak-to-trough (Terra+FTX)",  "2021-11-10", "2022-11-21"),
    ("COVID crash (Feb-Apr 2020)",            "2020-02-19", "2020-04-15"),
]
for label, start, end in windows:
    result = run(start, end)
    t = result["trades"]
    m = compute_metrics(t, CAPITAL, result["start"], result["end"])
    bh = btc_buy_and_hold(result["df"], CAPITAL)
    cap_hits = 0
    if not t.empty and "exit_reason" in t.columns:
        cap_hits = t["exit_reason"].astype(str).str.contains("capitulation", case=False, na=False).sum()
    print(f"  {label}")
    print(f"    trades={m['total_trades']:>4}  ann={m['annualized_return_pct']:>7.2f}%  "
          f"maxDD={m['max_drawdown_pct']:>6.2f}%  win_rate={m['win_rate']*100 if m['win_rate'] else 0:>5.1f}%  "
          f"BTC B&H={bh['total_return_pct']:>7.1f}%  capitulation_fires={cap_hits}")

print()
print("=" * 70)
print("PART 4 — Code review: done during the clean cutover itself")
print("=" * 70)
print("  Line-by-line audit of _run_blended_slots (fees, breakeven, capital")
print("  conservation, sentiment tilt, compounding) plus an independently")
print("  hand-traced position matching the real engine to full float")
print("  precision -- see prior session turns. Not re-run here.")
