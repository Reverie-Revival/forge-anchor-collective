"""
The Gauntlet (4-part), run against Model 2's pooled-reserve + BTC bucket
design (docs/decisions/008), src/backtester/model_engine.py's
run_pooled_model_backtest().

Part 1: single-year walk-forward (fixed-parameter config, no re-tuning per split)
Part 2: bootstrap distribution on the pool-only sequence -- worst/mid/best case.
        The BTC bucket's dip-buy/recover-principal mechanic is inherently
        path-dependent on the REAL BTC price history (drawdown_from_high_pct
        off real candles) -- there is no honest way to bootstrap-resample
        trade order and still know whether a real, usable dip existed at the
        resampled moments. Bootstrapping is applied to the pool-only P&L
        sequence (the genuinely path-dependent, reorderable part); the
        bucket's robustness is instead assessed by Part 1 (multiple real
        years) and Part 3 (real bear markets) below, both against real price.
Part 3: real historical bear-market tests -- also the main check for whether
        the floor/shrink logic (skipped_entries) actually engages, since the
        first full-history run never triggered it at all.
Part 4: code review of run_pooled_model_backtest() itself.
"""
import random
import pandas as pd

from src.app.db import load_stream_configs
from src.backtester.model_engine import run_pooled_model_backtest

DYNAMIC_SKIM = {"target_trades": 22, "avg_win_pct": 1.8, "min_skim_pct": 10.0, "max_skim_pct": 25.0}


def cfg(stream_id, version):
    return next(c for c in load_stream_configs(stream_id) if c["version"] == version)


def model2_streams():
    return [
        {"stream_id": 1, "stream_name": "Momentum Rider v4", "params": cfg(1, "v4")["params"], "lot_size_usd": 25.0, "slot_count": 1, "slot_mode": "single"},
        {"stream_id": 2, "stream_name": "Dip Hunter v3",      "params": cfg(2, "v3")["params"], "lot_size_usd": 25.0, "slot_count": 1, "slot_mode": "single"},
        {"stream_id": 3, "stream_name": "Breakout Scout v3",  "params": cfg(3, "v3")["params"], "lot_size_usd": 25.0, "slot_count": 1, "slot_mode": "single"},
        {"stream_id": 4, "stream_name": "Volume Raider v1",   "params": cfg(4, "v1")["params"], "lot_size_usd": 25.0, "slot_count": 1, "slot_mode": "single"},
    ]


def run(start, end=None):
    return run_pooled_model_backtest(model2_streams(), start=start, end=end, dynamic_skim=DYNAMIC_SKIM)


def ann_from_ending(baseline, ending, years_span):
    total_return = (ending - baseline) / baseline
    base = 1 + total_return
    return ((base ** (1 / years_span)) - 1) * 100 if base > 0 and years_span > 0 else None


print("=" * 78)
print("PART 1 — Single-year walk-forward (2018-2026 YTD)")
print("=" * 78)
years_results = []
for year in range(2018, 2027):
    start = f"{year}-01-01"
    end = None if year == 2026 else f"{year}-12-31"
    res = run(start, end)
    baseline = res["baseline_total"]
    ending = res["combined_ending"]
    years_span = 1.0 if year != 2026 else None
    if years_span is None:
        # partial year -- approximate from actual ledger span if any trades happened
        years_span = 0.6  # rough YTD fraction as of 2026-08; informational only
    ann = ann_from_ending(baseline, ending, years_span)
    n_trades = len(res["ledger"])
    years_results.append((year, ann, n_trades, res["skipped_entries"]))
    flag = "OK" if ann and ann > 0 else "*** NEGATIVE ***"
    print(f"  {year}: trades={n_trades:>3}  skipped={res['skipped_entries']:>2}  "
          f"end=${ending:>8,.2f}  ann={ann if ann is not None else float('nan'):>7.2f}%  {flag}")

n_positive = sum(1 for _, ann, _, _ in years_results if ann and ann > 0)
print(f"\n  {n_positive}/{len(years_results)} years positive.")


print()
print("=" * 78)
print("PART 2 — Bootstrap distribution (pool-only sequence): worst / mid / best case")
print("=" * 78)

full = run("2018-01-01", None)
baseline = full["baseline_total"]
ledger = full["ledger"].sort_values("exit_ts").reset_index(drop=True)
real_pool_ending = full["final_pool_balance"] + full["bucket"]["total_skimmed"]  # pool + what was skimmed out (undo the skim to isolate pool-only path)

# Reconstruct each trade's growth factor relative to the pool size AT THE TIME
# it happened (same method as gauntlet_gs_reflex.py) -- pool-only, pre-skim,
# since skim/bucket are the path-dependent-on-real-price part this method
# can't honestly capture.
pool = baseline
factors = []
for pnl in ledger["pnl"]:
    factor = 1 + (pnl / pool) if pool > 0 else 1.0
    factors.append(factor)
    pool += pnl

reconstructed = baseline
for f in factors:
    reconstructed *= f
print(f"  Reconstruction check: {reconstructed:.2f} vs pool-path {pool:.2f} -- "
      f"{'OK' if abs(reconstructed - pool) < 0.01 else 'MISMATCH'}\n")

random.seed(42)
N_RESAMPLES = 10000
n_trades = len(factors)
resampled_endings = []
for _ in range(N_RESAMPLES):
    capital = baseline
    for _ in range(n_trades):
        capital *= random.choice(factors)
    resampled_endings.append(capital)

resampled_endings.sort()
years_span = (full["ledger"]["exit_ts"].max() - full["ledger"]["entry_ts"].min()).days / 365.25


def pct(p):
    idx = int(len(resampled_endings) * p / 100)
    idx = min(idx, len(resampled_endings) - 1)
    return resampled_endings[idx]


p5, p50, p95 = pct(5), pct(50), pct(95)
n_losses = sum(1 for e in resampled_endings if e < baseline)
real_rank = sum(1 for e in resampled_endings if e <= pool) / len(resampled_endings) * 100

print(f"  Worst case (5th pct):  ${p5:>10,.2f}   ({ann_from_ending(baseline, p5, years_span):>7.2f}% ann.)")
print(f"  Mid case   (median):   ${p50:>10,.2f}   ({ann_from_ending(baseline, p50, years_span):>7.2f}% ann.)")
print(f"  Best case  (95th pct): ${p95:>10,.2f}   ({ann_from_ending(baseline, p95, years_span):>7.2f}% ann.)")
print(f"\n  Real historical pool-only result: ${pool:,.2f} -- {real_rank:.1f}th percentile of the distribution")
print(f"  {n_losses}/{N_RESAMPLES} resamples ({n_losses/N_RESAMPLES*100:.2f}%) ended below the ${baseline:.0f} baseline")
print(f"\n  NOT bootstrapped: the BTC bucket itself (real full-history contribution: "
      f"${full['bucket']['total_holdings_value']:,.2f} holdings from ${full['bucket']['total_skimmed']:,.2f} "
      f"skimmed) -- path-dependent on real BTC price, see Parts 1 & 3 instead.")


print()
print("=" * 78)
print("PART 3 — Real historical bear-market tests")
print("=" * 78)
windows = [
    ("2018 crypto winter (full year)",        "2018-01-01", "2018-12-31"),
    ("2022 bear (full calendar year)",        "2022-01-01", "2022-12-31"),
    ("2021-2022 peak-to-trough (Terra+FTX)",  "2021-11-10", "2022-11-21"),
    ("COVID crash (Feb-Apr 2020)",            "2020-02-19", "2020-04-15"),
]
for label, start, end in windows:
    res = run(start, end)
    baseline = res["baseline_total"]
    ending = res["combined_ending"]
    ret_pct = (ending - baseline) / baseline * 100
    print(f"  {label}")
    print(f"    trades={len(res['ledger']):>3}  skipped_entries={res['skipped_entries']:>2}  "
          f"pool=${res['final_pool_balance']:>8,.2f}  bucket=${res['bucket']['total_holdings_value']:>7,.2f}  "
          f"end=${ending:>8,.2f}  ret={ret_pct:>7.2f}%")


print()
print("=" * 78)
print("PART 4 — Code review: run_pooled_model_backtest() self-audit")
print("=" * 78)
print("""
  Checked directly against docs/decisions/008's agreed design and against
  the existing simulate_skim_bucket (bb18d45) it reuses:

  - Single-slot-only guard: raises ValueError for any slot_count != 1 or
    slot_mode != 'single' config, rather than silently mis-simulating a
    multi-slot stream through single-slot math. Correct -- staggered/
    cascade/blended each have real per-slot capital semantics this
    function does not model.
  - Entry/exit tie-break at identical timestamps: exits are processed
    before entries (event tuple sort key (ts, seq) with exit seq=0,
    entry seq=1) -- frees pool capital before sizing the next entry,
    the more conservative direction, documented inline.
  - roi = pnl / lot_size_usd is computed from each stream's OWN full-lot
    backtest, independent of the pooled capital ultimately used --
    valid only because entry/exit *timing* (signals, trailing-stop
    trigger prices) never depends on capital size anywhere in this
    codebase (confirmed: _run_slot's stop/signal logic is entirely
    price-and-% based). If that ever changes for any future stream type,
    this two-pass design breaks silently -- worth a code comment there
    (not yet added).
  - Skipped entries are truly skipped, not deferred: a signal that fires
    while a stream's computed share is < $10 produces no trade at all,
    and does not retry until that stream's OWN next signal. Matches the
    "kept simple" instruction directly.
  - Surplus-only skim math: new_surplus = surplus_after - surplus_before
    correctly handles the straddle case (a trade that partially rebuilds
    the pool to baseline and partially creates surplus) -- only the
    surplus portion is ever skimmed, verified by construction (surplus_
    before/after are both max(0, pool - baseline), so a trade that ends
    still below baseline always yields new_surplus == 0).
  - Bucket mechanics are a direct, unmodified copy of bb18d45's
    simulate_skim_bucket loop (dip entry / principal-recovery exit /
    house money) -- only the skims input timeline changed. No behavior
    drift from the already-reasoned-through original.

  Not yet reviewed: no unit test exists for run_pooled_model_backtest()
  itself (tests/ has no backtester-level test suite at all currently --
  a pre-existing gap in this project, not introduced here). Recommend
  adding at least one deterministic small-fixture test before this is
  trusted for a real deployment decision.
""")


print()
print("=" * 78)
print("PART 5 — Deliberate stress test: pool starting already below baseline")
print("=" * 78)
print("  Every run above started fresh at the $100 baseline and never once\n"
      "  triggered skipped_entries, even across real bear markets -- meaning\n"
      "  the floor/shrink logic (the actual point of this design) has never\n"
      "  been exercised. Forcing starting_pool below baseline against the\n"
      "  same real 2022 bear-market signals tests it directly, without a\n"
      "  fabricated loss sequence.\n")

BEAR_START, BEAR_END = "2022-01-01", "2022-12-31"
for starting_pool in [100.0, 70.0, 45.0, 41.0, 38.0]:
    res = run_pooled_model_backtest(model2_streams(), start=BEAR_START, end=BEAR_END,
                                    dynamic_skim=DYNAMIC_SKIM, starting_pool=starting_pool)
    ledger = res["ledger"]
    min_pool = ledger["pool_after"].min() if not ledger.empty else starting_pool
    halted = "HALTED" if res["halted_at"] is not None else "ok"
    print(f"  starting_pool=${starting_pool:>6.2f}  skipped_entries={res['skipped_entries']:>2}  "
          f"min_pool_seen=${min_pool:>7.2f}  final_pool=${res['final_pool_balance']:>7.2f}  "
          f"end=${res['combined_ending']:>7.2f}  hard_floor=${res['hard_floor']:.2f}  [{halted}]")
