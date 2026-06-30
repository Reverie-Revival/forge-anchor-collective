"""
Programmatic entry point for stream-level backtesting.
Run from Claude Code — results are written to a file the Streamlit app watches.
"""
import pickle
import os
import json
import hashlib
from pathlib import Path

from .engine import run_backtest, SLOT_MODES
from .metrics import compute_metrics, btc_buy_and_hold

LAST_RUN_PATH = Path(__file__).parent.parent / "app" / ".last_run.pkl"
RUNS_DIR      = Path(__file__).parent.parent / "app" / "runs"


def _strip_none(obj):
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj


def _params_hash(params: dict) -> str:
    return hashlib.md5(json.dumps(_strip_none(params), sort_keys=True).encode()).hexdigest()[:10]


def run(
    params: dict,
    stream_name: str,
    start: str = None,
    end: str = None,
    slot_count: int = 1,
    slot_mode: str = 'single',
    lot_size_usd: float = 10.0,
) -> dict:
    """
    Run a stream backtest and push results to the Streamlit app.
    Returns a summary dict for display in the conversation.

    slot_mode: 'single' | 'scale_down' | 'scale_up'
    lot_size_usd: capital per slot (total = lot_size_usd × slot_count)
    """
    initial_capital = lot_size_usd * slot_count

    result  = run_backtest(
        params,
        start=start,
        end=end,
        slot_count=slot_count,
        slot_mode=slot_mode,
        stream_name=stream_name,
        lot_size_usd=lot_size_usd,
    )
    trades  = result["trades"]
    df      = result["df"]
    metrics = compute_metrics(trades, initial_capital, result["start"], result["end"])
    bh      = btc_buy_and_hold(df, initial_capital)

    ending_balance = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)

    payload = {
        "stream_name":     stream_name,
        "params":          params,
        "result":          result,
        "trades":          trades,
        "df":              df,
        "metrics":         metrics,
        "bh":              bh,
        "initial_capital": initial_capital,
        "ending_balance":  ending_balance,
        "slot_count":      slot_count,
        "slot_mode":       slot_mode,
        "lot_size_usd":    lot_size_usd,
    }

    with open(LAST_RUN_PATH, "wb") as f:
        pickle.dump(payload, f)

    RUNS_DIR.mkdir(exist_ok=True)
    ph        = _params_hash(params)
    start_str = str(result["start"])[:10]
    end_str   = str(result["end"])[:10]
    pending   = RUNS_DIR / f"pending_{ph}_{start_str}_{end_str}.pkl"
    with open(pending, "wb") as f:
        pickle.dump(payload, f)

    ann = metrics["annualized_return_pct"]
    return {
        "stream":             stream_name,
        "period":             f"{result['start'].date()} → {result['end'].date()}",
        "slot_count":         slot_count,
        "slot_mode":          slot_mode,
        "lot_size_usd":       f"${lot_size_usd:.2f}",
        "initial_capital":    f"${initial_capital:.2f}",
        "signals":            int(result["signals"].sum()),
        "trades":             metrics["total_trades"],
        "ending_balance":     f"${ending_balance:.2f}",
        "total_pnl":          f"${metrics['total_pnl']:+.2f}",
        "total_return":       f"{metrics['total_return_pct']:.1f}%",
        "annualized_return":  f"{ann:.1f}%" if ann else "—",
        "win_rate":           f"{round(metrics['win_rate']*100, 1)}%" if metrics["win_rate"] else "—",
        "profit_factor":      str(metrics["profit_factor"]) if metrics["profit_factor"] else "—",
        "avg_winner":         f"{metrics['avg_winner_pct']}%" if metrics["avg_winner_pct"] else "—",
        "avg_loser":          f"{metrics['avg_loser_pct']}%" if metrics["avg_loser_pct"] else "—",
        "max_drawdown":       f"{metrics['max_drawdown_pct']}%" if metrics["max_drawdown_pct"] else "—",
        "avg_hold_hrs":       f"{round(metrics['avg_hold_candles'] * 0.25, 1)}h" if metrics["avg_hold_candles"] else "—",
        "btc_bh_annualized":  f"{bh['annualized_return_pct']}%",
    }
