import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import create_engine, text

import pickle
from pathlib import Path

from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold

LAST_RUN_PATH = Path(__file__).parent / ".last_run.pkl"


def get_engine():
    return create_engine("postgresql+psycopg2://localhost/forge_anchor")


def save_stream_test(stream_name, params, result, metrics, initial_capital, ending_balance, notes=""):
    engine = get_engine()
    name_parts = stream_name.rsplit(" ", 1)
    version = name_parts[1] if len(name_parts) == 2 and name_parts[1].startswith("v") else "v1"
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO backtest.stream_tests (
                stream_name, stream_version, parameters,
                test_start, test_end, n_slots, initial_capital, ending_balance,
                total_trades, win_rate, total_pnl, total_return_pct,
                annualized_return_pct, avg_winner_pct, avg_loser_pct,
                profit_factor, max_drawdown_pct, avg_hold_candles, notes
            ) VALUES (
                :stream_name, :stream_version, :parameters,
                :test_start, :test_end, :n_slots, :initial_capital, :ending_balance,
                :total_trades, :win_rate, :total_pnl, :total_return_pct,
                :annualized_return_pct, :avg_winner_pct, :avg_loser_pct,
                :profit_factor, :max_drawdown_pct, :avg_hold_candles, :notes
            )
        """), {
            "stream_name":           name_parts[0].strip(),
            "stream_version":        version,
            "parameters":            json.dumps(params),
            "test_start":            result["start"],
            "test_end":              result["end"],
            "n_slots":               result.get("n_slots", 2),
            "initial_capital":       initial_capital,
            "ending_balance":        ending_balance,
            "total_trades":          metrics["total_trades"],
            "win_rate":              metrics["win_rate"],
            "total_pnl":             metrics["total_pnl"],
            "total_return_pct":      metrics["total_return_pct"],
            "annualized_return_pct": metrics["annualized_return_pct"],
            "avg_winner_pct":        metrics["avg_winner_pct"],
            "avg_loser_pct":         metrics["avg_loser_pct"],
            "profit_factor":         metrics["profit_factor"],
            "max_drawdown_pct":      metrics["max_drawdown_pct"],
            "avg_hold_candles":      metrics["avg_hold_candles"],
            "notes":                 notes,
        })
        conn.commit()

st.set_page_config(page_title="Stream Tester", layout="wide", page_icon="⚓")
st.title("⚓ Forge Anchor — Stream Tester")

# ── Glossary ───────────────────────────────────────────────────────────────────
GLOSSARY = {
    "EMA (Exponential Moving Average)":
        "A running average of price that gives more weight to recent candles. "
        "EMA 9 reacts quickly; EMA 200 moves slowly. When a fast EMA crosses above a slow one, "
        "it signals that short-term momentum has turned bullish.",
    "SMA (Simple Moving Average)":
        "A plain average of price over N candles. Used as a trend filter — if price is above the "
        "200 SMA, BTC is in a long-term uptrend.",
    "RSI (Relative Strength Index)":
        "A 0–100 score measuring how overbought or oversold BTC is. Below 35 = oversold (potential bounce). "
        "Above 70 = overbought (potential pullback).",
    "ATR (Average True Range)":
        "Measures how much BTC moves per candle on average. High ATR = volatile market. "
        "Low ATR = quiet, consolidating market.",
    "Trailing Stop":
        "Instead of a fixed exit price, the stop follows price upward and only locks in when price "
        "drops by a set percentage from the highest point reached. Lets winners run while capping losses.",
    "Candle / 15m Candle":
        "One bar of price data covering 15 minutes — has an open, high, low, and close price plus volume. "
        "The full dataset has one candle every 15 minutes from Jan 2017 to now.",
    "Slot":
        "Each stream runs in 2 independent slots, each with $10. They use the same strategy but trade "
        "independently — one might be in a trade while the other is waiting for a signal.",
}

with st.expander("📖 Glossary — terms used in this app"):
    for term, definition in GLOSSARY.items():
        st.markdown(f"**{term}** — {definition}")

# ── Preset stream configs ──────────────────────────────────────────────────────

PRESETS = {
    "Momentum Rider v1": {
        "core_signal": "ema_crossover",
        "core_params": {"ema_short": 9, "ema_long": 21},
        "filters": {
            "trend_context": {"sma_period": 200, "require": "above"},
            "rsi": None, "volume": None, "atr_regime": None, "bollinger": None,
        },
        "position": {"trailing_stop_pct": 3.0, "entry_order_type": "limit",
                     "entry_expiry_candles": 2, "partial_exit": None,
                     "max_hold_candles": None, "min_hold_candles": None},
    },
    "Dip Hunter v1": {
        "core_signal": "rsi_dip",
        "core_params": {"rsi_period": 14, "rsi_threshold": 35, "sma_period": 20, "dip_pct": 2.0},
        "filters": {
            "trend_context": None, "rsi": None, "volume": None, "atr_regime": None, "bollinger": None,
        },
        "position": {"trailing_stop_pct": 2.5, "entry_order_type": "limit",
                     "entry_expiry_candles": 1, "partial_exit": None,
                     "max_hold_candles": None, "min_hold_candles": None},
    },
    "Breakout Scout v1": {
        "core_signal": "range_breakout",
        "core_params": {"breakout_lookback": 48},
        "filters": {
            "trend_context": None, "rsi": None, "volume": None,
            "atr_regime": {"period": 14, "avg_period": 30, "max_pct_of_avg": 70},
            "bollinger": None,
        },
        "position": {"trailing_stop_pct": 4.0, "entry_order_type": "limit",
                     "entry_expiry_candles": 2, "partial_exit": None,
                     "max_hold_candles": None, "min_hold_candles": None},
    },
    "Surge Rider v1": {
        "core_signal": "volume_surge",
        "core_params": {"volume_avg_period": 20, "volume_multiplier": 2.5},
        "filters": {
            "trend_context": None,
            "rsi": {"period": 14, "min": 45, "max": 70},
            "volume": {"avg_period": 20, "min_multiplier": 2.5},
            "atr_regime": None, "bollinger": None,
        },
        "position": {"trailing_stop_pct": 2.0, "entry_order_type": "limit",
                     "entry_expiry_candles": 1, "partial_exit": None,
                     "max_hold_candles": None, "min_hold_candles": None},
    },
    "Steady Climber v1": {
        "core_signal": "sma_pullback",
        "core_params": {"trend_sma": 200, "pullback_sma": 50, "pullback_tolerance_pct": 1.5},
        "filters": {
            "trend_context": {"sma_period": 200, "require": "above"},
            "rsi": None, "volume": None, "atr_regime": None, "bollinger": None,
        },
        "position": {"trailing_stop_pct": 3.5, "entry_order_type": "limit",
                     "entry_expiry_candles": 3, "partial_exit": None,
                     "max_hold_candles": None, "min_hold_candles": None},
    },
}

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Stream Config")

    preset_name = st.selectbox("Stream", list(PRESETS.keys()))
    preset = PRESETS[preset_name]
    pos = preset["position"]
    core_p = preset["core_params"]
    filters = preset.get("filters", {})
    core_signal = preset["core_signal"]

    st.divider()
    st.subheader("Date Range")
    use_full = st.checkbox("Full dataset (Jan 2017 → now)", value=True)
    if not use_full:
        col_s, col_e = st.columns(2)
        with col_s:
            start_date = st.date_input("From", value=pd.Timestamp("2020-01-01"))
        with col_e:
            end_date = st.date_input("To", value=pd.Timestamp("2024-01-01"))
    else:
        start_date = None
        end_date = None

    n_slots = st.radio(
        "Slots",
        [1, 2],
        index=1,
        horizontal=True,
        help="Each slot is $10. 2 slots = $20 total starting capital for this stream."
    )

    st.divider()
    st.subheader("Position")
    trailing_stop = st.slider(
        "Trailing stop (%)",
        0.5, 10.0, float(pos["trailing_stop_pct"]), 0.25,
        help="Exit when price drops this % below its highest point since entry. "
             "Tighter = exits sooner. Wider = lets winners run longer but gives back more on reversal."
    )
    entry_expiry = st.slider(
        "Entry expiry (candles)",
        1, 10, int(pos["entry_expiry_candles"]),
        help="How many 15-min candles to wait for a limit order to fill before cancelling it."
    )

    st.divider()
    st.subheader("Signal Parameters")

    if core_signal == "ema_crossover":
        ema_short = st.slider("EMA short period", 3, 50, int(core_p["ema_short"]),
            help="Fast EMA. Smaller = reacts faster to price changes, more signals, more noise.")
        ema_long = st.slider("EMA long period", 5, 200, int(core_p["ema_long"]),
            help="Slow EMA. Signal fires when EMA short crosses above EMA long.")
        trend_sma_on = st.checkbox("Only trade above 200 SMA (uptrend filter)",
            value=filters.get("trend_context") is not None,
            help="If checked, this stream only enters trades when BTC is in a long-term uptrend.")

    elif core_signal == "rsi_dip":
        rsi_threshold = st.slider("RSI entry threshold (enter below)", 10, 50, int(core_p["rsi_threshold"]),
            help="Enter when RSI drops below this number. Lower = only enter on deeper oversold conditions.")
        dip_pct = st.slider("Dip % below SMA required", 0.5, 10.0, float(core_p["dip_pct"]), 0.25,
            help="Price must be at least this far below its moving average before entering.")
        sma_period = st.slider("SMA period", 5, 100, int(core_p["sma_period"]),
            help="The moving average used to measure how far price has dipped.")

    elif core_signal == "range_breakout":
        breakout_lookback = st.slider("Breakout lookback (candles)", 12, 192, int(core_p["breakout_lookback"]),
            help="Enter when price breaks above its highest point over this many candles. "
                 "48 candles = 12 hours.")
        atr_filter_on = st.checkbox("Require low-volatility consolidation first",
            value=filters.get("atr_regime") is not None,
            help="Only enter breakouts that follow a quiet, consolidating period. "
                 "Reduces fakeout breakouts.")
        if atr_filter_on:
            atr_threshold = st.slider("ATR max % of its average", 30, 100, 70,
                help="70 means ATR must be below 70% of its recent average — i.e. market was quiet.")
        else:
            atr_threshold = 70

    elif core_signal == "volume_surge":
        vol_multiplier = st.slider("Volume spike multiplier", 1.0, 5.0, float(core_p["volume_multiplier"]), 0.25,
            help="Enter when volume is this many times its recent average. "
                 "2.5x means unusually high participation.")
        rsi_min = st.slider("RSI minimum (momentum floor)", 20, 60,
            int((filters.get("rsi") or {}).get("min", 45)),
            help="Don't enter if RSI is below this — means the volume spike lacks upward momentum.")
        rsi_max = st.slider("RSI maximum (overbought ceiling)", 50, 90,
            int((filters.get("rsi") or {}).get("max", 70)),
            help="Don't enter if RSI is above this — means the move may already be exhausted.")

    elif core_signal == "sma_pullback":
        pullback_sma = st.slider("Pullback target SMA period", 10, 100,
            int(core_p.get("pullback_sma", 50)),
            help="Enter when price pulls back to touch this moving average during an uptrend.")
        tolerance_pct = st.slider("Proximity tolerance (%)", 0.5, 5.0,
            float(core_p.get("pullback_tolerance_pct", 1.5)), 0.25,
            help="How close to the SMA price needs to be to count as a 'touch'.")

    st.divider()
    run_btn = st.button("▶ Run Backtest", type="primary", use_container_width=True)


# ── Build params from sidebar ──────────────────────────────────────────────────

def build_params():
    p = {
        "core_signal": core_signal,
        "core_params": {},
        "filters": {"trend_context": None, "rsi": None, "volume": None, "atr_regime": None, "bollinger": None},
        "regime": None, "timeframe_confirmation": None, "time_filters": None,
        "position": {
            "trailing_stop_pct": trailing_stop,
            "entry_order_type": "limit",
            "entry_expiry_candles": entry_expiry,
            "partial_exit": None, "max_hold_candles": None, "min_hold_candles": None,
        },
        "pause_rules": None, "sentiment": None, "on_chain": None,
    }

    if core_signal == "ema_crossover":
        p["core_params"] = {"ema_short": ema_short, "ema_long": ema_long}
        if trend_sma_on:
            p["filters"]["trend_context"] = {"sma_period": 200, "require": "above"}

    elif core_signal == "rsi_dip":
        p["core_params"] = {"rsi_period": 14, "rsi_threshold": rsi_threshold,
                            "sma_period": sma_period, "dip_pct": dip_pct}

    elif core_signal == "range_breakout":
        p["core_params"] = {"breakout_lookback": breakout_lookback}
        if atr_filter_on:
            p["filters"]["atr_regime"] = {"period": 14, "avg_period": 30, "max_pct_of_avg": atr_threshold}

    elif core_signal == "volume_surge":
        p["core_params"] = {"volume_avg_period": 20, "volume_multiplier": vol_multiplier}
        p["filters"]["rsi"] = {"period": 14, "min": rsi_min, "max": rsi_max}
        p["filters"]["volume"] = {"avg_period": 20, "min_multiplier": vol_multiplier}

    elif core_signal == "sma_pullback":
        p["core_params"] = {"trend_sma": 200, "pullback_sma": pullback_sma,
                            "pullback_tolerance_pct": tolerance_pct}
        p["filters"]["trend_context"] = {"sma_period": 200, "require": "above"}

    return p


# ── Main ───────────────────────────────────────────────────────────────────────

# Load from file (written by Claude Code) or run manually via button
payload = None

if LAST_RUN_PATH.exists():
    try:
        with open(LAST_RUN_PATH, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        payload = None

if run_btn:
    params = build_params()
    start_str = str(start_date) if start_date else None
    end_str   = str(end_date)   if end_date   else None
    initial_capital = n_slots * 10.0

    with st.spinner("Running backtest..."):
        result  = run_backtest(params, start=start_str, end=end_str,
                               n_slots=n_slots, stream_name=preset_name)
        trades  = result["trades"]
        df      = result["df"]
        metrics = compute_metrics(trades, initial_capital, result["start"], result["end"])

    ending_capital_run = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)
    payload = {
        "stream_name": preset_name, "params": params, "result": result,
        "trades": trades, "df": df, "metrics": metrics,
        "initial_capital": initial_capital, "ending_balance": ending_capital_run,
    }
    with open(LAST_RUN_PATH, "wb") as f:
        pickle.dump(payload, f)

if payload:
    result          = payload["result"]
    trades          = payload["trades"]
    df              = payload["df"]
    metrics         = payload["metrics"]
    params          = payload["params"]
    initial_capital = payload["initial_capital"]
    ending_capital  = payload["ending_balance"]
    display_name    = payload["stream_name"]
    period_str      = f"{result['start'].date()} → {result['end'].date()}"

    st.caption(
        f"**{display_name}**  |  {period_str}  |  "
        f"{len(df):,} candles  |  {result['signals'].sum()} signals fired  |  "
        f"{metrics['total_trades']} trades closed"
    )

    if trades.empty or metrics["total_trades"] == 0:
        st.warning("No trades were generated with these settings.")
        st.stop()

    closed = trades[trades["exit_reason"] != "partial"].sort_values("exit_ts").copy()
    closed["return_pct"] = (closed["exit_price"] - closed["entry_price"]) / closed["entry_price"] * 100

    # ── Balance metrics (top of page) ─────────────────────────────────────────
    ann = metrics["annualized_return_pct"]

    st.subheader("Results")
    b1, b2, b3 = st.columns(3)
    b1.metric(
        "Starting Balance",
        f"${initial_capital:.2f}",
        help=f"{n_slots} slot(s) × $10 each"
    )
    b2.metric(
        "Ending Balance",
        f"${ending_capital:.2f}",
        delta=f"${ending_capital - initial_capital:+.2f}",
        help="Total value at end of backtest period, after all trades and fees."
    )
    b3.metric(
        "Total Return",
        f"{metrics['total_return_pct']:.1f}%",
        delta=f"{ann:.1f}% / year" if ann else None,
        help="Total profit or loss over the full period. "
             "Delta shows annualized rate (compounded yearly equivalent)."
    )

    st.divider()

    # ── Detail metrics ─────────────────────────────────────────────────────────
    m1, m2, m3, m4, m5 = st.columns(5)

    m1.metric(
        "Win Rate",
        f"{round(metrics['win_rate']*100, 1)}%" if metrics["win_rate"] else "—",
        help="Percentage of trades that closed in profit. "
             "50% means half your trades won. Higher is better, but size of wins matters too."
    )
    m2.metric(
        "Profit Factor",
        str(metrics["profit_factor"]) if metrics["profit_factor"] else "—",
        help="Total dollars won ÷ total dollars lost. "
             "Above 1.0 = profitable overall. 1.5 means you made $1.50 for every $1 you lost."
    )
    m3.metric(
        "Avg Winner",
        f"{metrics['avg_winner_pct']}%" if metrics["avg_winner_pct"] else "—",
        help="Average % gain on winning trades."
    )
    m4.metric(
        "Avg Loser",
        f"{metrics['avg_loser_pct']}%" if metrics["avg_loser_pct"] else "—",
        help="Average % loss on losing trades. Ideally this is smaller (in absolute terms) than Avg Winner."
    )
    m5.metric(
        "Avg Hold",
        f"{metrics['avg_hold_candles']} candles" if metrics["avg_hold_candles"] else "—",
        help=f"Average time in a trade. "
             f"{round(metrics['avg_hold_candles'] * 0.25, 1)} hours on average."
             if metrics["avg_hold_candles"] else "Average number of 15-min candles held per trade."
    )

    st.divider()

    # ── Equity curve ──────────────────────────────────────────────────────────
    equity = initial_capital + closed["pnl"].cumsum()

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=closed["exit_ts"], y=equity,
        name=preset_name,
        line=dict(color="#00d4aa", width=2),
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Balance: $%{y:.2f}<extra></extra>"
    ))
    fig_eq.add_hline(
        y=initial_capital,
        line=dict(color="#888", dash="dash"),
        annotation_text=f"Starting ${initial_capital:.0f}"
    )
    fig_eq.update_layout(
        template="plotly_dark",
        title="Account Balance Over Time",
        xaxis_title="Date",
        yaxis_title="Balance ($)",
        height=380,
        margin=dict(t=40, b=20),
        showlegend=False
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    col_dd, col_dist = st.columns(2)

    # ── Drawdown ──────────────────────────────────────────────────────────────
    with col_dd:
        peak = equity.cummax()
        drawdown = (equity - peak) / peak * 100
        worst = drawdown.min()

        fig_dd = go.Figure(go.Scatter(
            x=closed["exit_ts"], y=drawdown,
            fill="tozeroy",
            line=dict(color="#ff4d4d", width=1),
            fillcolor="rgba(255,77,77,0.15)",
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>%{y:.1f}% below peak<extra></extra>"
        ))
        fig_dd.update_layout(
            template="plotly_dark",
            title=f"Drawdown from Peak  (worst: {worst:.1f}%)",
            xaxis_title="Date",
            yaxis_title="% below peak",
            height=300,
            margin=dict(t=40, b=20)
        )
        st.plotly_chart(fig_dd, use_container_width=True)
        st.caption(
            "**How to read this:** 0% means you're at your all-time high balance. "
            f"-20% means you dropped 20% below your best point before recovering. "
            f"Worst drop here was {worst:.1f}%. Closer to 0 is better."
        )

    # ── Return distribution ───────────────────────────────────────────────────
    with col_dist:
        winners = closed[closed["return_pct"] > 0]["return_pct"]
        losers  = closed[closed["return_pct"] <= 0]["return_pct"]

        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=winners, name="Winners",
            marker_color="#00d4aa", opacity=0.75,
            xbins=dict(size=0.5)
        ))
        fig_hist.add_trace(go.Histogram(
            x=losers, name="Losers",
            marker_color="#ff4d4d", opacity=0.75,
            xbins=dict(size=0.5)
        ))
        fig_hist.update_layout(
            template="plotly_dark",
            title="Return Distribution (how big were wins vs losses?)",
            xaxis_title="Return per trade (%)",
            yaxis_title="Number of trades",
            barmode="overlay",
            height=300,
            margin=dict(t=40, b=20),
            legend=dict(x=0.75, y=0.99)
        )
        st.plotly_chart(fig_hist, use_container_width=True)
        st.caption(
            "Each bar = how many trades landed in that return range. "
            "You want the green bars (wins) to be taller and further right than red bars (losses)."
        )

    # ── Trade log ─────────────────────────────────────────────────────────────
    with st.expander(f"Trade Log ({len(closed)} trades)", expanded=False):
        log = closed.copy()
        log["btc_bought"]     = (log["capital"] / log["entry_price"]).round(6)
        log["start_value"]    = log["capital"].round(2)
        log["end_value"]      = (log["capital"] + log["pnl"]).round(2)
        log["gain_loss"]      = log["pnl"].round(4)
        log["return_pct"]     = log["return_pct"].round(2)
        log["entry_price"]    = log["entry_price"].round(2)
        log["exit_price"]     = log["exit_price"].round(2)
        log["duration_hrs"]   = (log["candles_held"] * 0.25).round(1)

        display = log[[
            "slot", "entry_ts", "exit_ts",
            "entry_price", "exit_price",
            "btc_bought",
            "start_value", "end_value", "gain_loss",
            "return_pct",
            "duration_hrs", "exit_reason"
        ]].rename(columns={
            "slot":         "Slot",
            "entry_ts":     "Entered",
            "exit_ts":      "Exited",
            "entry_price":  "BTC Price In",
            "exit_price":   "BTC Price Out",
            "btc_bought":   "BTC Bought",
            "start_value":  "$ In",
            "end_value":    "$ Out",
            "gain_loss":    "P&L ($)",
            "return_pct":   "Return %",
            "duration_hrs": "Hours Held",
            "exit_reason":  "Exit Reason",
        })

        st.dataframe(display, use_container_width=True)

    # ── Save ──────────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("💾 Save This Run")
    save_notes = st.text_input(
        "Notes (optional)",
        placeholder="e.g. tightened trailing stop, added 200 SMA filter — much better drawdown"
    )
    if st.button("Save to Database", type="secondary"):
        try:
            save_stream_test(
                stream_name=display_name,
                params=params,
                result=result,
                metrics=metrics,
                initial_capital=initial_capital,
                ending_balance=ending_capital,
                notes=save_notes,
            )
            st.success(f"Saved! **{display_name}** — {metrics['total_trades']} trades, "
                       f"{metrics['annualized_return_pct']}% annualized, "
                       f"${ending_capital:.2f} ending balance.")
        except Exception as e:
            st.error(f"Save failed: {e}")

elif not run_btn:
    st.info("Waiting for a run from Claude Code, or use the sidebar to run manually.")
