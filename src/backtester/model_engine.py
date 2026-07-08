"""
Model-level backtest engine.
Runs all locked streams simultaneously with their configured allocations and aggregates results.
"""
import pandas as pd

from .engine import run_backtest
from .metrics import compute_metrics, btc_buy_and_hold


def run_model_backtest(stream_configs: list, start: str = None, end: str = None) -> dict:
    """
    Run a combined model backtest across all streams.

    stream_configs: list of dicts, each with:
        stream_id    (int)
        stream_name  (str)   — full name including version, e.g. "Momentum Rider v1"
        params       (dict)  — backtest params from backtest.streams
        lot_size_usd (float) — capital per slot
        slot_count   (int)   — max concurrent positions for this stream
        slot_mode    (str)   — 'single' | 'scale_down' | 'scale_up'

    Returns a combined payload dict with per-stream results and aggregated metrics.
    """
    stream_results = []
    all_trade_frames = []
    reference_df = None

    for sc in stream_configs:
        initial_capital = sc["lot_size_usd"] * sc["slot_count"]
        result = run_backtest(
            sc["params"],
            start=start,
            end=end,
            slot_count=sc["slot_count"],
            slot_mode=sc.get("slot_mode", "single"),
            stream_name=sc["stream_name"],
            lot_size_usd=sc["lot_size_usd"],
        )
        trades  = result["trades"]
        metrics = compute_metrics(trades, initial_capital, result["start"], result["end"])
        ending_balance = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)

        if reference_df is None:
            reference_df = result["df"]

        if not trades.empty:
            t = trades.copy()
            t["stream_name"] = sc["stream_name"]
            t["stream_id"]   = sc["stream_id"]
            all_trade_frames.append(t)

        stream_results.append({
            "stream_id":        sc["stream_id"],
            "stream_config_id": sc.get("stream_config_id"),
            "stream_name":      sc["stream_name"],
            "lot_size_usd":    sc["lot_size_usd"],
            "slot_count":      sc["slot_count"],
            "slot_mode":       sc.get("slot_mode", "single"),
            "initial_capital": initial_capital,
            "ending_balance":  ending_balance,
            "result":          result,
            "trades":          trades,
            "metrics":         metrics,
        })

    total_capital = sum(sr["initial_capital"] for sr in stream_results)
    period_start  = stream_results[0]["result"]["start"] if stream_results else pd.Timestamp(start)
    period_end    = stream_results[-1]["result"]["end"]   if stream_results else pd.Timestamp(end)

    if all_trade_frames:
        combined_trades = (
            pd.concat(all_trade_frames)
            .sort_values("entry_ts")
            .reset_index(drop=True)
        )
    else:
        combined_trades = pd.DataFrame()

    combined_metrics = compute_metrics(combined_trades, total_capital, period_start, period_end)
    bh = btc_buy_and_hold(reference_df, total_capital) if reference_df is not None else {}

    return {
        "stream_results":   stream_results,
        "combined_trades":  combined_trades,
        "combined_metrics": combined_metrics,
        "total_capital":    total_capital,
        "bh":               bh,
        "start":            period_start,
        "end":              period_end,
    }
