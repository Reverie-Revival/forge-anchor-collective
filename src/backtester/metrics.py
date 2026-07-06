import pandas as pd
import numpy as np


def compute_metrics(trades: pd.DataFrame, initial_capital: float, start, end) -> dict:
    """Compute performance metrics from a trades DataFrame."""
    empty = {
        "total_trades": 0,
        "win_rate": None, "total_pnl": 0.0, "total_return_pct": 0.0,
        "annualized_return_pct": None, "avg_winner_pct": None, "avg_loser_pct": None,
        "profit_factor": None, "max_drawdown_pct": None, "avg_hold_candles": None,
        "sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None,
        "avg_mae_pct": None, "avg_mfe_pct": None,
        "max_consec_losses": None,
    }
    if trades.empty:
        return empty

    t = trades[trades["exit_reason"] != "partial"].copy()
    t["return_pct"] = (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100

    winners = t[t["pnl"] > 0]
    losers  = t[t["pnl"] <= 0]

    total_pnl    = trades["pnl"].sum()
    win_rate     = len(winners) / len(t) if len(t) else None
    avg_winner   = winners["return_pct"].mean() if len(winners) else None
    avg_loser    = losers["return_pct"].mean()  if len(losers)  else None
    gross_profit = winners["pnl"].sum() if len(winners) else 0
    gross_loss   = abs(losers["pnl"].sum()) if len(losers) else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    # equity curve → max drawdown
    equity   = initial_capital + trades.sort_values("exit_ts")["pnl"].cumsum()
    peak     = equity.cummax()
    drawdown = (equity - peak) / peak * 100
    max_drawdown = drawdown.min()

    # annualized return
    total_days = (pd.Timestamp(end) - pd.Timestamp(start)).days if start and end else None
    total_return_pct = total_pnl / initial_capital * 100
    annualized = None
    if total_days and total_days > 0:
        years = total_days / 365.25
        base  = 1 + total_return_pct / 100
        if base > 0:
            annualized = (base ** (1 / years) - 1) * 100

    avg_hold = t["candles_held"].mean() if len(t) else None

    # --- risk-adjusted ratios (per-trade approach) ---
    sharpe = sortino = calmar = None
    if len(t) >= 2 and total_days and total_days > 0:
        returns = t["return_pct"].values / 100
        years   = total_days / 365.25
        tpy     = len(returns) / years          # trades per year
        mean_r  = np.mean(returns)
        std_r   = np.std(returns, ddof=1)
        if std_r > 0:
            sharpe = round((mean_r / std_r) * np.sqrt(tpy), 2)
        neg = returns[returns < 0]
        if len(neg) >= 1:
            down_std = np.std(neg, ddof=1) if len(neg) > 1 else abs(neg[0])
            if down_std > 0:
                sortino = round((mean_r / down_std) * np.sqrt(tpy), 2)
    if annualized is not None and max_drawdown and max_drawdown != 0:
        calmar = round(annualized / abs(max_drawdown), 2)

    # --- MAE / MFE ---
    avg_mae = avg_mfe = None
    if "mae_pct" in t.columns:
        avg_mae = round(t["mae_pct"].mean(), 2) if t["mae_pct"].notna().any() else None
    if "mfe_pct" in t.columns:
        avg_mfe = round(t["mfe_pct"].mean(), 2) if t["mfe_pct"].notna().any() else None

    # --- max consecutive losses ---
    max_consec = 0
    cur_streak = 0
    for pnl in t.sort_values("exit_ts")["pnl"]:
        if pnl <= 0:
            cur_streak += 1
            max_consec = max(max_consec, cur_streak)
        else:
            cur_streak = 0

    return {
        "total_trades":         len(t),
        "win_rate":             win_rate,
        "total_pnl":            round(total_pnl, 4),
        "total_return_pct":     round(total_return_pct, 2),
        "annualized_return_pct": round(annualized, 2) if annualized is not None else None,
        "avg_winner_pct":       round(avg_winner, 2) if avg_winner is not None else None,
        "avg_loser_pct":        round(avg_loser,  2) if avg_loser  is not None else None,
        "profit_factor":        round(profit_factor, 2) if profit_factor is not None else None,
        "max_drawdown_pct":     round(max_drawdown, 2) if max_drawdown is not None else None,
        "avg_hold_candles":     round(avg_hold, 1)     if avg_hold      is not None else None,
        "sharpe_ratio":         sharpe,
        "sortino_ratio":        sortino,
        "calmar_ratio":         calmar,
        "avg_mae_pct":          avg_mae,
        "avg_mfe_pct":          avg_mfe,
        "max_consec_losses":    max_consec if len(t) > 0 else None,
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
