import pandas as pd
import numpy as np


def compute_metrics(trades: pd.DataFrame, initial_capital: float, start, end) -> dict:
    """Compute performance metrics from a trades DataFrame."""
    if trades.empty:
        return {
            "total_trades": 0,
            "win_rate": None,
            "total_pnl": 0.0,
            "total_return_pct": 0.0,
            "annualized_return_pct": None,
            "avg_winner_pct": None,
            "avg_loser_pct": None,
            "profit_factor": None,
            "max_drawdown_pct": None,
            "avg_hold_candles": None,
        }

    t = trades[trades["exit_reason"] != "partial"].copy()
    t["return_pct"] = (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100

    winners = t[t["pnl"] > 0]
    losers = t[t["pnl"] <= 0]

    total_pnl = trades["pnl"].sum()
    win_rate = len(winners) / len(t) if len(t) else None

    avg_winner = winners["return_pct"].mean() if len(winners) else None
    avg_loser = losers["return_pct"].mean() if len(losers) else None

    gross_profit = winners["pnl"].sum() if len(winners) else 0
    gross_loss = abs(losers["pnl"].sum()) if len(losers) else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    # equity curve for drawdown
    equity = initial_capital + trades.sort_values("exit_ts")["pnl"].cumsum()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak * 100
    max_drawdown = drawdown.min()

    # annualized return
    total_days = (pd.Timestamp(end) - pd.Timestamp(start)).days if start and end else None
    total_return_pct = total_pnl / initial_capital * 100
    annualized = None
    if total_days and total_days > 0:
        years = total_days / 365.25
        base = 1 + total_return_pct / 100
        if base > 0:
            annualized = (base ** (1 / years) - 1) * 100

    avg_hold = t["candles_held"].mean() if len(t) else None

    return {
        "total_trades": len(t),
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 4),
        "total_return_pct": round(total_return_pct, 2),
        "annualized_return_pct": round(annualized, 2) if annualized is not None else None,
        "avg_winner_pct": round(avg_winner, 2) if avg_winner is not None else None,
        "avg_loser_pct": round(avg_loser, 2) if avg_loser is not None else None,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown, 2) if max_drawdown is not None else None,
        "avg_hold_candles": round(avg_hold, 1) if avg_hold is not None else None,
    }


def btc_buy_and_hold(df: pd.DataFrame, initial_capital: float) -> dict:
    """Return metrics for BTC buy-and-hold over the same period."""
    start_price = df["close"].iloc[0]
    end_price = df["close"].iloc[-1]
    total_return_pct = (end_price - start_price) / start_price * 100
    days = (df.index[-1] - df.index[0]).days
    years = days / 365.25
    annualized = ((1 + total_return_pct / 100) ** (1 / years) - 1) * 100 if years > 0 else None
    return {
        "start_price": round(start_price, 2),
        "end_price": round(end_price, 2),
        "total_return_pct": round(total_return_pct, 2),
        "annualized_return_pct": round(annualized, 2) if annualized is not None else None,
    }
