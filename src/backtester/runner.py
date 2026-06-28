"""
Programmatic entry point for conversation-driven backtesting.
Run from Claude Code — results are written to a file the Streamlit app watches.
"""
import pickle
import os
from pathlib import Path

from .engine import run_backtest
from .metrics import compute_metrics, btc_buy_and_hold

LAST_RUN_PATH = Path(__file__).parent.parent / "app" / ".last_run.pkl"


def run(params: dict, stream_name: str, start: str = None, end: str = None, n_slots: int = 2) -> dict:
    """
    Run a backtest and push results to the Streamlit app.
    Returns a summary dict for display in the conversation.
    """
    initial_capital = n_slots * 10.0

    result  = run_backtest(params, start=start, end=end, n_slots=n_slots, stream_name=stream_name)
    trades  = result["trades"]
    df      = result["df"]
    metrics = compute_metrics(trades, initial_capital, result["start"], result["end"])
    bh      = btc_buy_and_hold(df, initial_capital)

    ending_balance = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)

    payload = {
        "stream_name":    stream_name,
        "params":         params,
        "result":         result,
        "trades":         trades,
        "df":             df,
        "metrics":        metrics,
        "bh":             bh,
        "initial_capital": initial_capital,
        "ending_balance": ending_balance,
    }

    with open(LAST_RUN_PATH, "wb") as f:
        pickle.dump(payload, f)

    ann = metrics["annualized_return_pct"]
    return {
        "stream":             stream_name,
        "period":             f"{result['start'].date()} → {result['end'].date()}",
        "signals":            int(result["signals"].sum()),
        "trades":             metrics["total_trades"],
        "starting_balance":   f"${initial_capital:.2f}",
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
