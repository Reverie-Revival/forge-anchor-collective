import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import json
import hashlib
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
import pickle
from pathlib import Path
import yfinance as yf

LAST_RUN_PATH = Path(__file__).parent / ".last_run.pkl"
RUNS_DIR      = Path(__file__).parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)

SP500_HISTORICAL_AVG = 10.0


# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def fetch_sp500(start: str, end: str):
    try:
        spy = yf.download("^GSPC", start=start, end=end, auto_adjust=True, progress=False)
        if spy.empty:
            return None
        s = float(spy["Close"].iloc[0].iloc[0] if hasattr(spy["Close"].iloc[0], "iloc") else spy["Close"].iloc[0])
        e = float(spy["Close"].iloc[-1].iloc[0] if hasattr(spy["Close"].iloc[-1], "iloc") else spy["Close"].iloc[-1])
        total = (e - s) / s * 100
        days  = (pd.Timestamp(end) - pd.Timestamp(start)).days
        years = days / 365.25
        ann   = ((1 + total / 100) ** (1 / years) - 1) * 100 if years > 0 else None
        return {"total_return_pct": round(total, 2), "annualized_return_pct": round(ann, 2) if ann else None}
    except Exception:
        return None


def load_run_payload(test_id: int):
    pkl_path = RUNS_DIR / f"{test_id}.pkl"
    if not pkl_path.exists():
        return None
    with open(pkl_path, "rb") as f:
        return pickle.load(f)


def get_engine():
    return create_engine("postgresql+psycopg2://localhost/forge_anchor")


def candle_hours(params: dict) -> float:
    return {"1h": 1.0, "4h": 4.0, "1d": 24.0}.get(params.get("primary_timeframe"), 0.25)


def grade_info(ann):
    if ann is None:  return None, "No grade", "#555555"
    if ann >= 20:    return 5, "Grade 5 · Elite",   "#00d4aa"
    if ann >= 10:    return 4, "Grade 4 · Strong",  "#4ade80"
    if ann >= 8:     return 3, "Grade 3 · Passing", "#facc15"
    if ann > 0:      return 2, "Grade 2 · Weak",    "#fb923c"
    return           1,        "Grade 1 · Poor",    "#f87171"


def params_hash(params: dict) -> str:
    return hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]


def label_window(start_date, end_date) -> str:
    today = pd.Timestamp.now().date()
    s = pd.Timestamp(start_date).date()
    e = pd.Timestamp(end_date).date()
    is_recent_end = (today - e).days <= 60
    if s.year <= 2017 and is_recent_end:
        return "Full History"
    if s.year == 2019 and e.year == 2023:
        return "Primary"
    if s.year >= 2026 and is_recent_end:
        return "Recent"
    return f"{s.strftime('%b %Y')} → {e.strftime('%b %Y')}"


def _compact_config(params: dict) -> str:
    core      = params.get("core_signal", "")
    core_p    = params.get("core_params") or {}
    pos       = params.get("position") or {}
    tf        = params.get("primary_timeframe") or "15m"
    filters   = params.get("filters") or {}
    sentiment = params.get("sentiment") or {}

    if core == "ema_crossover":
        sig = f"{core_p.get('ema_short')}/{core_p.get('ema_long')} EMA"
    elif core == "rsi_dip":
        sig = f"RSI dip <{core_p.get('rsi_threshold')}"
    elif core == "range_breakout":
        sig = f"{core_p.get('breakout_lookback')}-candle breakout"
    elif core == "volume_surge":
        sig = f"{core_p.get('volume_multiplier')}× vol surge"
    elif core == "sma_pullback":
        sig = f"pullback to {core_p.get('pullback_sma')} SMA"
    else:
        sig = core

    parts = [f"{sig} · {tf}"]
    active = []
    tc = filters.get("trend_context")
    if tc:
        active.append(f">{tc.get('sma_period')} SMA")
    rsi_f = filters.get("rsi")
    if rsi_f:
        if rsi_f.get("min") is not None: active.append(f"RSI>{rsi_f['min']}")
        if rsi_f.get("max") is not None: active.append(f"RSI<{rsi_f['max']}")
    if filters.get("atr_regime"):
        active.append("low-vol")
    fg = (sentiment.get("fear_greed") or {})
    if fg.get("max") is not None: active.append(f"F&G<{fg['max']}")
    if fg.get("min") is not None: active.append(f"F&G>{fg['min']}")
    if active:
        parts.append(" · ".join(active))
    trail = pos.get("trailing_stop_pct")
    if trail:
        parts.append(f"{trail}% trail")
    return "  ·  ".join(parts)


def _human_readable_description(params: dict) -> str:
    core      = params.get("core_signal", "")
    core_p    = params.get("core_params") or {}
    filters   = params.get("filters") or {}
    pos       = params.get("position") or {}
    sentiment = params.get("sentiment") or {}
    tf        = params.get("primary_timeframe")

    tf_desc = {
        "1h":  "hourly candles (4× less frequent than raw 15m — much less noise)",
        "4h":  "4-hour candles (very selective — only catches larger, sustained moves)",
        "1d":  "daily candles (only major trend-level shifts trigger an entry)",
    }.get(tf, "15-minute candles (maximum granularity)")

    if core == "ema_crossover":
        s, l = core_p.get("ema_short"), core_p.get("ema_long")
        core_sent = (
            f"Enters when the {s}-period EMA crosses above the {l}-period EMA — "
            f"the short-term trend overtaking the longer-term trend, signaling a momentum shift. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "rsi_dip":
        thresh = core_p.get("rsi_threshold", 35)
        dip    = core_p.get("dip_pct", 2.0)
        smap   = core_p.get("sma_period", 20)
        core_sent = (
            f"Enters when RSI drops below {thresh} (oversold) and price is at least {dip}% "
            f"below its {smap}-period SMA — looking for genuine panic dips likely to bounce. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "range_breakout":
        lb  = core_p.get("breakout_lookback", 48)
        hrs = lb * 0.25
        core_sent = (
            f"Enters when price breaks above its highest point over the last {lb} candles ({hrs:.0f}h) — "
            f"the moment a consolidation range gives way. Evaluated on {tf_desc}."
        )
    elif core == "volume_surge":
        mult = core_p.get("volume_multiplier", 2.5)
        core_sent = (
            f"Enters on a volume spike of {mult}× the recent average with a bullish candle. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "sma_pullback":
        psma = core_p.get("pullback_sma", 50)
        tol  = core_p.get("pullback_tolerance_pct", 1.5)
        core_sent = (
            f"Enters when price pulls back to within {tol}% of the {psma}-period SMA during an uptrend. "
            f"Evaluated on {tf_desc}."
        )
    else:
        core_sent = f"Core signal: {core}. Evaluated on {tf_desc}."

    filter_sents = []
    tc = filters.get("trend_context")
    if tc:
        req = "above" if tc.get("require", "above") == "above" else "below"
        filter_sents.append(
            f"Only enters when BTC is {req} its {tc.get('sma_period', 200)}-period SMA — "
            f"aligning with the long-term trend."
        )
    rsi_f = filters.get("rsi")
    if rsi_f:
        rsi_parts = []
        if rsi_f.get("min") is not None: rsi_parts.append(f"above {rsi_f['min']} (momentum present)")
        if rsi_f.get("max") is not None: rsi_parts.append(f"below {rsi_f['max']} (not yet overbought)")
        if rsi_parts:
            filter_sents.append(f"RSI must be {' and '.join(rsi_parts)} at entry.")
    if filters.get("atr_regime"):
        max_pct = filters["atr_regime"].get("max_pct_of_avg", 70)
        filter_sents.append(
            f"Requires ATR below {max_pct}% of its recent average — only enters after calm consolidation, not chaos."
        )
    fg = (sentiment.get("fear_greed") or {})
    if fg.get("min") is not None:
        filter_sents.append(
            f"Fear & Greed must be above {fg['min']} — only trading when sentiment supports momentum, not during panic."
        )
    if fg.get("max") is not None:
        filter_sents.append(
            f"Fear & Greed must be below {fg['max']} — avoiding euphoric markets where momentum entries tend to top out."
        )

    exit_sents = []
    trail = pos.get("trailing_stop_pct")
    if trail:
        exit_sents.append(
            f"Exits via a {trail}% trailing stop — follows price upward, only triggers if BTC drops "
            f"{trail}% from its peak since entry."
        )
    if pos.get("min_hold_candles"):
        exit_sents.append(f"Holds at least {pos['min_hold_candles']} candles before the stop can fire.")
    if pos.get("max_hold_candles"):
        exit_sents.append(f"Force-exits after {pos['max_hold_candles']} candles regardless.")

    return " ".join([core_sent] + filter_sents + exit_sents)


# ── Database ──────────────────────────────────────────────────────────────────

def load_stream_history() -> pd.DataFrame:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            return pd.read_sql(text("""
                SELECT test_id, stream_name, stream_version,
                       run_number, window_name, parameters,
                       test_start, test_end, total_trades, profit_factor,
                       annualized_return_pct, max_drawdown_pct,
                       avg_winner_pct, avg_loser_pct, win_rate,
                       total_return_pct, ending_balance, initial_capital,
                       saved_at, notes
                FROM backtest.stream_tests
                ORDER BY run_number ASC, saved_at ASC
            """), conn)
    except Exception:
        return pd.DataFrame()


KNOWN_STREAMS = [
    "Momentum Rider v1",
    "Dip Hunter v1",
    "Breakout Scout v1",
    "Steady Climber v1",
    "Surge Rider v1",
]


def next_run_number(stream_nm: str, params_h: str, history: pd.DataFrame) -> int:
    """Return existing run_number if this params hash is already saved, else assign next."""
    stream_rows = history[history["stream_name"] == stream_nm] if not history.empty else pd.DataFrame()
    if not stream_rows.empty:
        for _, row in stream_rows.iterrows():
            try:
                p = row["parameters"] if isinstance(row["parameters"], dict) \
                    else json.loads(row["parameters"])
                if params_hash(p) == params_h:
                    return int(row["run_number"])
            except Exception:
                pass
    existing = stream_rows["run_number"].dropna()
    return int(existing.max()) + 1 if not existing.empty else 1


def save_stream_test(stream_name, params, result, metrics, initial_capital, ending_balance,
                     payload: dict, window_name: str = "", notes: str = "",
                     history: pd.DataFrame = None) -> tuple:
    engine     = get_engine()
    name_parts = stream_name.rsplit(" ", 1)
    version    = name_parts[1] if len(name_parts) == 2 and name_parts[1].startswith("v") else "v1"
    stream_nm  = name_parts[0].strip()
    p_hash     = params_hash(params)
    hist       = history if history is not None else pd.DataFrame()

    run_num = next_run_number(stream_nm, p_hash, hist)
    win_nm  = window_name or label_window(result["start"], result["end"])

    with engine.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO backtest.stream_tests (
                stream_name, stream_version, run_number, window_name, parameters,
                test_start, test_end, n_slots, initial_capital, ending_balance,
                total_trades, win_rate, total_pnl, total_return_pct,
                annualized_return_pct, avg_winner_pct, avg_loser_pct,
                profit_factor, max_drawdown_pct, avg_hold_candles, notes
            ) VALUES (
                :stream_name, :stream_version, :run_number, :window_name, :parameters,
                :test_start, :test_end, :n_slots, :initial_capital, :ending_balance,
                :total_trades, :win_rate, :total_pnl, :total_return_pct,
                :annualized_return_pct, :avg_winner_pct, :avg_loser_pct,
                :profit_factor, :max_drawdown_pct, :avg_hold_candles, :notes
            ) RETURNING test_id
        """), {
            "stream_name":           stream_nm,
            "stream_version":        version,
            "run_number":            run_num,
            "window_name":           win_nm,
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
        test_id = row.scalar()
        conn.commit()

    pkl_path = RUNS_DIR / f"{test_id}.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(payload, f)

    # Clean up matching pending file if it exists
    ph = params_hash(params)
    start_str = str(result["start"])[:10]
    end_str   = str(result["end"])[:10]
    pending   = RUNS_DIR / f"pending_{ph}_{start_str}_{end_str}.pkl"
    if pending.exists():
        pending.unlink()

    return test_id, run_num, win_nm


# ── Dashboard renderer ────────────────────────────────────────────────────────

def render_dashboard(payload: dict, show_save: bool = True, key_prefix: str = "dash"):
    result          = payload["result"]
    trades          = payload["trades"]
    metrics         = payload["metrics"]
    params          = payload["params"]
    bh              = payload.get("bh", {})
    initial_capital = payload["initial_capital"]
    ending_capital  = payload["ending_balance"]
    display_name    = payload["stream_name"]
    period_str      = f"{result['start'].date()} → {result['end'].date()}"
    tf              = params.get("primary_timeframe") or "15m"
    c_hrs           = candle_hours(params)
    ann             = metrics["annualized_return_pct"]
    _, grade_label, grade_color = grade_info(ann)

    if trades.empty or metrics["total_trades"] == 0:
        st.warning("No trades were generated with these settings.")
        return

    closed = trades[trades["exit_reason"] != "partial"].sort_values("exit_ts").copy()
    closed["return_pct"] = (closed["exit_price"] - closed["entry_price"]) / closed["entry_price"] * 100

    # Header
    col_title, col_grade = st.columns([3, 1])
    with col_title:
        st.subheader(display_name)
        st.caption(
            f"{period_str}  ·  {tf} candles  ·  "
            f"{result['signals'].sum()} signals  ·  {metrics['total_trades']} trades"
        )
    with col_grade:
        st.markdown(
            f'<div style="text-align:right;margin-top:8px;">'
            f'<span class="grade-badge" style="background:{grade_color}22;'
            f'color:{grade_color};border:1px solid {grade_color}66;font-size:1rem;">'
            f'{grade_label}</span></div>',
            unsafe_allow_html=True
        )

    st.divider()

    # Performance KPIs
    st.markdown('<p class="section-label">Performance</p>', unsafe_allow_html=True)

    sp500_actual = fetch_sp500(str(result["start"].date()), str(result["end"].date()))
    sp500_ann    = sp500_actual["annualized_return_pct"] if sp500_actual else None
    bh_ann       = bh.get("annualized_return_pct")

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Starting Balance", f"${initial_capital:.2f}",
              help=f"{result.get('n_slots', 2)} slots × $10 each.")
    h2.metric("Ending Balance", f"${ending_capital:.2f}",
              delta=f"{ending_capital - initial_capital:+.2f}",
              delta_color="normal",
              help="Total value after all trades and fees.")
    h3.metric(
        "Annualized Return",
        f"{ann:+.1f}%" if ann is not None else "—",
        delta=f"{ann - SP500_HISTORICAL_AVG:+.1f}% vs S&P historical avg" if ann is not None else None,
        delta_color="normal",
        help=f"Compounded yearly return. S&P 500 long-run average: ~{SP500_HISTORICAL_AVG:.0f}%."
    )
    h4.metric(
        "Total Return",
        f"{metrics['total_return_pct']:+.1f}%",
        delta=f"{ann:+.1f}% / year" if ann is not None else None,
        delta_color="normal",
        help=f"Cumulative return over {period_str}."
    )

    # Benchmarks
    st.markdown('<p class="section-label" style="margin-top:16px;">Benchmarks — how did we compare?</p>',
                unsafe_allow_html=True)
    b1, b2, b3 = st.columns(3)
    b1.metric(
        "S&P 500 historical avg",
        f"{SP500_HISTORICAL_AVG:.0f}% / year",
        delta=f"{ann - SP500_HISTORICAL_AVG:+.1f}% vs us" if ann is not None else None,
        delta_color="normal",
        help="Long-run average annualized S&P 500 return (~10%). The baseline to beat."
    )
    b2.metric(
        f"S&P 500 actual ({period_str})",
        f"{sp500_ann:+.1f}% / year" if sp500_ann is not None else "—",
        delta=f"{ann - sp500_ann:+.1f}% vs us" if (ann is not None and sp500_ann is not None) else None,
        delta_color="normal",
        help="What S&P 500 actually returned during this exact backtest period."
    )
    b3.metric(
        "BTC buy & hold",
        f"{bh_ann:+.1f}% / year" if bh_ann is not None else "—",
        delta=f"{ann - bh_ann:+.1f}% vs us" if (ann is not None and bh_ann is not None) else None,
        delta_color="normal",
        help="What you'd have made just holding BTC for the same period."
    )

    st.divider()

    # Trade stats
    st.markdown('<p class="section-label">Trade Statistics</p>', unsafe_allow_html=True)
    s1, s2, s3, s4, s5, s6 = st.columns(6)

    n_wins   = len(closed[closed["pnl"] > 0])
    n_losses = len(closed[closed["pnl"] <= 0])
    s1.metric("Win Rate",
              f"{metrics['win_rate']*100:.1f}%" if metrics["win_rate"] else "—",
              delta=f"{n_wins} wins · {n_losses} losses",
              delta_color="off",
              help="% of trades that ended in profit.")
    s2.metric("Profit Factor",
              str(metrics["profit_factor"]) if metrics["profit_factor"] else "—",
              help="Total $ won ÷ total $ lost. >1.0 = profitable.")
    s3.metric("Max Drawdown",
              f"{metrics['max_drawdown_pct']:.1f}%" if metrics["max_drawdown_pct"] is not None else "—",
              delta="worst peak-to-trough drop", delta_color="off",
              help="Largest drop from peak balance. Lower (less negative) is better.")
    avg_hold_hrs = round(metrics["avg_hold_candles"] * c_hrs, 1) if metrics["avg_hold_candles"] else None
    s4.metric("Avg Hold", f"{avg_hold_hrs}h" if avg_hold_hrs else "—",
              help="Average time in a trade.")
    s5.metric("Avg Winner",
              f"{metrics['avg_winner_pct']:+.1f}%" if metrics["avg_winner_pct"] else "—",
              help="Average % return on winning trades.")
    s6.metric("Avg Loser",
              f"{metrics['avg_loser_pct']:.1f}%" if metrics["avg_loser_pct"] else "—",
              help="Average % loss on losing trades.")

    # Equity curve
    equity  = initial_capital + closed["pnl"].cumsum()
    peak_val = equity.max()
    peak_ts  = closed.loc[equity.idxmax(), "exit_ts"]
    low_val  = equity.min()
    low_ts   = closed.loc[equity.idxmin(), "exit_ts"]

    curve_color = "#00d4aa" if ending_capital >= initial_capital else "#f87171"
    fill_color  = "rgba(0,212,170,0.07)" if ending_capital >= initial_capital else "rgba(248,113,113,0.07)"

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=closed["exit_ts"], y=equity,
        fill="tozeroy", fillcolor=fill_color,
        line=dict(color=curve_color, width=2.5),
        hovertemplate="<b>%{x|%b %d, %Y}</b><br>Balance: $%{y:.2f}<extra></extra>"
    ))
    fig_eq.add_hline(y=initial_capital, line=dict(color="#555", dash="dot"),
                     annotation_text=f"Start ${initial_capital:.0f}",
                     annotation_font_color="#888")
    fig_eq.add_trace(go.Scatter(
        x=[peak_ts], y=[peak_val], mode="markers+text",
        marker=dict(color="#4ade80", size=10, symbol="circle"),
        text=[f"  <b>High ${peak_val:.2f}</b><br>  {peak_ts.strftime('%b %d, %Y')}"],
        textposition="middle right", textfont=dict(color="#4ade80", size=12),
        hovertemplate=f"<b>Peak</b><br>{peak_ts.strftime('%b %d, %Y')}<br>${peak_val:.2f}<extra></extra>",
        showlegend=False,
    ))
    fig_eq.add_trace(go.Scatter(
        x=[low_ts], y=[low_val], mode="markers+text",
        marker=dict(color="#f87171", size=10, symbol="circle"),
        text=[f"  <b>Low ${low_val:.2f}</b><br>  {low_ts.strftime('%b %d, %Y')}"],
        textposition="middle right", textfont=dict(color="#f87171", size=12),
        hovertemplate=f"<b>Trough</b><br>{low_ts.strftime('%b %d, %Y')}<br>${low_val:.2f}<extra></extra>",
        showlegend=False,
    ))
    if sp500_ann is not None:
        sp_end = initial_capital * ((1 + sp500_ann / 100) ** ((result["end"] - result["start"]).days / 365.25))
        fig_eq.add_annotation(
            x=closed["exit_ts"].iloc[-1], y=sp_end,
            text=f"S&P 500 this period: ${sp_end:.2f}",
            showarrow=False, font=dict(color="#f59e0b", size=11), xanchor="right"
        )
    fig_eq.update_layout(
        template="plotly_dark", title="Account Balance Over Time",
        xaxis_title=None, yaxis_title="Balance ($)",
        height=360, margin=dict(t=40, b=20, l=10, r=10),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_eq, use_container_width=True, key=f"{key_prefix}_eq")

    col_dd, col_dist = st.columns(2)

    with col_dd:
        peak_eq  = equity.cummax()
        drawdown = (equity - peak_eq) / peak_eq * 100
        worst    = drawdown.min()
        fig_dd   = go.Figure(go.Scatter(
            x=closed["exit_ts"], y=drawdown,
            fill="tozeroy", line=dict(color="#f87171", width=1.5),
            fillcolor="rgba(248,113,113,0.12)",
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>%{y:.1f}% below peak<extra></extra>"
        ))
        fig_dd.update_layout(
            template="plotly_dark", title=f"Drawdown  (worst: {worst:.1f}%)",
            xaxis_title=None, yaxis_title="% below peak",
            height=280, margin=dict(t=40, b=20, l=10, r=10),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_dd, use_container_width=True, key=f"{key_prefix}_dd")
        st.caption(f"0% = at all-time high. Worst drop: **{worst:.1f}%** before recovering.")

    with col_dist:
        trade_seq    = list(range(1, len(closed) + 1))
        colors       = ["#4ade80" if r > 0 else "#f87171" for r in closed["return_pct"]]
        hover_labels = [
            f"Trade #{i}<br>{row.exit_ts.strftime('%b %d, %Y')}<br>{row.return_pct:+.2f}%"
            for i, row in zip(trade_seq, closed.itertuples())
        ]
        fig_trades = go.Figure()
        fig_trades.add_hline(y=0, line=dict(color="#555", width=1))
        fig_trades.add_trace(go.Bar(
            x=trade_seq, y=closed["return_pct"],
            marker_color=colors, hovertext=hover_labels, hoverinfo="text",
        ))
        fig_trades.update_layout(
            template="plotly_dark", title="Every Trade — Return %",
            xaxis_title="Trade #", yaxis_title="Return (%)",
            height=280, margin=dict(t=40, b=20, l=10, r=10),
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_trades, use_container_width=True, key=f"{key_prefix}_trades")
        st.caption(
            f"Each bar is one trade in order. Green = win, red = loss. "
            f"Avg winner **{metrics['avg_winner_pct']:+.1f}%** · avg loser **{metrics['avg_loser_pct']:.1f}%**."
            if metrics["avg_winner_pct"] and metrics["avg_loser_pct"] else
            "Each bar is one trade. Green = win, red = loss."
        )

    with st.expander(f"Trade Log ({len(closed)} trades)", expanded=False):
        log = closed.copy()
        log["btc_bought"]   = (log["capital"] / log["entry_price"]).round(6)
        log["start_value"]  = log["capital"].round(2)
        log["end_value"]    = (log["capital"] + log["pnl"]).round(2)
        log["gain_loss"]    = log["pnl"].round(4)
        log["return_pct"]   = log["return_pct"].round(2)
        log["entry_price"]  = log["entry_price"].round(2)
        log["exit_price"]   = log["exit_price"].round(2)
        log["duration_hrs"] = (log["candles_held"] * c_hrs).round(1)
        display = log[[
            "slot", "entry_ts", "exit_ts",
            "entry_price", "exit_price", "btc_bought",
            "start_value", "end_value", "gain_loss",
            "return_pct", "duration_hrs", "exit_reason"
        ]].rename(columns={
            "slot": "Slot", "entry_ts": "Entered", "exit_ts": "Exited",
            "entry_price": "BTC In", "exit_price": "BTC Out",
            "btc_bought": "BTC Bought",
            "start_value": "$ In", "end_value": "$ Out",
            "gain_loss": "P&L ($)", "return_pct": "Return %",
            "duration_hrs": "Hours Held", "exit_reason": "Exit Reason",
        })
        st.dataframe(display, use_container_width=True)

    if show_save:
        st.divider()
        st.subheader("💾 Save This Run")
        auto_window = label_window(result["start"], result["end"])
        sc1, sc2 = st.columns([1, 2])
        save_window = sc1.text_input("Window name", value=auto_window,
                                     key=f"{key_prefix}_window",
                                     help="Label for this date range (e.g. Primary Window, Full History, Recent)")
        save_notes  = sc2.text_input("Notes (optional)",
                                     key=f"{key_prefix}_notes",
                                     placeholder="e.g. RSI>55 cuts noise — better PF and lower drawdown")
        if st.button("Save to Database", type="secondary", key=f"{key_prefix}_save"):
            try:
                history_df = load_stream_history()
                test_id, run_num, win_nm = save_stream_test(
                    stream_name=display_name, params=params, result=result,
                    metrics=metrics, initial_capital=initial_capital,
                    ending_balance=ending_capital, payload=payload,
                    window_name=save_window, notes=save_notes, history=history_df,
                )
                st.success(
                    f"Saved — Run #{run_num} · {win_nm} · **{display_name}** · "
                    f"{metrics['total_trades']} trades · "
                    f"{metrics['annualized_return_pct']:+.1f}% annualized."
                )
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")


# ── Page config & CSS ──────────────────────────────────────────────────────────

st.set_page_config(page_title="Stream Tester", layout="wide", page_icon="⚓")

st.markdown("""
<style>
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 18px 20px 14px 20px;
}
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
.grade-badge {
    display: inline-block;
    padding: 6px 18px;
    border-radius: 20px;
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    margin-bottom: 4px;
}
.section-label {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #888;
    margin: 0 0 8px 0;
}
.config-group-header {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #aaa;
    margin-top: 12px;
    margin-bottom: 2px;
}
</style>
""", unsafe_allow_html=True)

st.title("⚓ Forge Anchor — Stream Tester")

# ── Glossary ───────────────────────────────────────────────────────────────────
GLOSSARY = {
    "EMA (Exponential Moving Average)":
        "A running average that weights recent prices more heavily. When a fast EMA (e.g. 20-period) "
        "crosses above a slow EMA (e.g. 50-period), short-term momentum has flipped bullish.",
    "SMA (Simple Moving Average)":
        "A plain average of closing prices over N candles. Used as a trend filter — price above the "
        "200 SMA means BTC is in a long-term uptrend.",
    "RSI (Relative Strength Index)":
        "0–100 score of momentum. Below 35 = oversold (potential bounce). Above 70 = overbought "
        "(potential pullback). Between 45–65 is the 'active momentum' zone.",
    "ATR (Average True Range)":
        "How much BTC moves per candle on average. High ATR = volatile. Low ATR = quiet/consolidating.",
    "Primary Timeframe":
        "The candle size used to evaluate signals. 15m = most signals, most noise. "
        "1h = 4× less frequent, much cleaner. 4h/1d = very selective. "
        "Raw data is always 15m — resampling happens at run time.",
    "Trailing Stop":
        "Follows price upward, only exits if price drops N% from its highest point since entry. "
        "Lets winning trades run while capping the downside.",
    "Fear & Greed Index":
        "Daily 0–100 crypto sentiment score. 0 = Extreme Fear, 100 = Extreme Greed. "
        "Can be used as an entry gate — e.g. only enter when F&G > 25 to avoid panic-driven false signals.",
    "Profit Factor":
        "Total $ won ÷ total $ lost. Above 1.0 = profitable overall. 1.5 means you made $1.50 "
        "for every $1 you lost, regardless of win rate.",
    "Slot":
        "Each stream runs in 2 independent $10 slots — same strategy, separate capital pools. "
        "One can be in a trade while the other waits for the next signal.",
}

with st.expander("📖 Glossary"):
    cols = st.columns(2)
    items = list(GLOSSARY.items())
    for i, (term, defn) in enumerate(items):
        cols[i % 2].markdown(f"**{term}**  \n{defn}")


# ── Load data ─────────────────────────────────────────────────────────────────

history = load_stream_history()

# Group saved tests by run_number within each stream
# run_groups[stream_name][run_number] = [list of test rows]
run_groups = {}
if not history.empty:
    for _, row in history.iterrows():
        skey = f"{row['stream_name']} {row['stream_version']}"
        rnum = int(row["run_number"]) if pd.notna(row.get("run_number")) else 0
        run_groups.setdefault(skey, {}).setdefault(rnum, []).append(row)

# All 5 streams always visible; any stream with history also shows even if not in KNOWN_STREAMS
all_streams = list(dict.fromkeys(KNOWN_STREAMS + [s for s in run_groups if s not in KNOWN_STREAMS]))

has_unsaved = LAST_RUN_PATH.exists()

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Stream Tester")
    st.caption("Triggered from Claude Code · Save to record here")

    # ── Stream selector ───────────────────────────────────────────────────────
    st.markdown('<p class="config-group-header">Stream</p>', unsafe_allow_html=True)
    if not all_streams:
        st.info("No streams yet.")
        st.stop()

    selected_stream = st.selectbox("", all_streams, label_visibility="collapsed")
    stream_runs     = run_groups.get(selected_stream, {})  # run_number → [rows]

    # ── Test Run selector ─────────────────────────────────────────────────────
    st.markdown('<p class="config-group-header" style="margin-top:14px;">Test Run</p>',
                unsafe_allow_html=True)

    run_options = []
    run_labels  = {}

    for rnum in sorted(stream_runs.keys()):
        rows = stream_runs[rnum]
        best = max((r["annualized_return_pct"] for r in rows if pd.notna(r["annualized_return_pct"])),
                   default=None)
        n_win = len(rows)
        lbl   = f"#{rnum}"
        if best is not None:
            lbl += f"  ·  {best:+.1f}%"
        if n_win > 1:
            lbl += f"  ·  {n_win} windows"
        run_options.append(rnum)
        run_labels[rnum] = lbl

    if not run_options:
        st.caption("No saved runs for this stream yet.")
        selected_run = None
    else:
        selected_run = st.selectbox(
            "", run_options,
            index=len(run_options) - 1,
            format_func=lambda x: run_labels.get(x, str(x)),
            label_visibility="collapsed",
        )

    # ── Config details ────────────────────────────────────────────────────────
    if selected_run and selected_run != "__new__" and selected_run in stream_runs:
        st.divider()
        rows = stream_runs[selected_run]
        try:
            p        = rows[0]["parameters"] if isinstance(rows[0]["parameters"], dict) \
                       else json.loads(rows[0]["parameters"])
            compact  = _compact_config(p)
            readable = _human_readable_description(p)
        except Exception:
            compact  = "—"
            readable = "—"

        st.markdown(f"**{rows[0]['stream_name']} {rows[0]['stream_version']}**")
        st.caption(compact)
        st.markdown(f"*{readable}*")
        st.divider()

        for row in rows:
            win  = row.get("window_name") or label_window(row["test_start"], row["test_end"])
            ann  = row["annualized_return_pct"]
            pf   = row["profit_factor"]
            _, gl, gc = grade_info(ann if pd.notna(ann) else None)
            st.markdown(
                f'<span class="grade-badge" style="background:{gc}20;color:{gc};'
                f'border:1px solid {gc}55;font-size:0.75rem;padding:3px 10px;">'
                f'{win}  ·  {ann:+.1f}%</span>' if pd.notna(ann) else
                f'<span class="grade-badge" style="background:#33333380;color:#aaa;'
                f'border:1px solid #55555555;font-size:0.75rem;padding:3px 10px;">'
                f'{win}</span>',
                unsafe_allow_html=True
            )
            st.caption(
                f"PF {pf:.2f}  ·  DD {row['max_drawdown_pct']:.1f}%  ·  "
                f"WR {row['win_rate']*100:.0f}%  ·  {row['total_trades']} trades"
                if pd.notna(pf) else "—"
            )
            if row.get("notes"):
                st.caption(f"💬 {row['notes']}")



# ── Main area ─────────────────────────────────────────────────────────────────

def load_pending_runs(run_rows: list) -> list:
    """Find pending_*.pkl files in RUNS_DIR matching the params hash of this run group."""
    if not run_rows:
        return []
    try:
        p  = run_rows[0]["parameters"] if isinstance(run_rows[0]["parameters"], dict) \
             else json.loads(run_rows[0]["parameters"])
        ph = params_hash(p)
    except Exception:
        return []

    # Collect saved date windows so we don't show a pending that's already saved
    saved_windows = set()
    for row in run_rows:
        saved_windows.add((str(row["test_start"])[:10], str(row["test_end"])[:10]))

    pending = []
    for f in sorted(RUNS_DIR.glob(f"pending_{ph}_*.pkl"), key=lambda x: x.stat().st_mtime):
        # filename: pending_{hash}_{start}_{end}.pkl
        parts = f.stem.split("_")  # ['pending', hash, start-date, end-date]
        if len(parts) >= 4:
            start_str = parts[2]
            end_str   = parts[3]
            if (start_str, end_str) not in saved_windows:
                try:
                    with open(f, "rb") as fh:
                        payload = pickle.load(fh)
                    pending.append({"file": f, "payload": payload,
                                    "start": start_str, "end": end_str})
                except Exception:
                    pass
    return pending


# ── Main area ─────────────────────────────────────────────────────────────────

if selected_run is not None and selected_run in stream_runs:
    saved_rows   = stream_runs[selected_run]
    pending_runs = load_pending_runs(saved_rows)

    # Build tab list: saved windows first, then unsaved pending
    tab_entries = []
    for row in saved_rows:
        lbl = row.get("window_name") or label_window(row["test_start"], row["test_end"])
        tab_entries.append({"label": lbl, "type": "saved", "data": row})
    for pr in pending_runs:
        lbl = label_window(pr["start"], pr["end"])
        tab_entries.append({"label": f"⏳ {lbl}", "type": "pending", "data": pr})

    # Deduplicate labels
    seen = {}
    for entry in tab_entries:
        lbl = entry["label"]
        seen[lbl] = seen.get(lbl, 0) + 1
        if seen[lbl] > 1:
            entry["label"] = f"{lbl} ({seen[lbl]})"

    tabs = st.tabs([e["label"] for e in tab_entries])

    for tab, entry in zip(tabs, tab_entries):
        with tab:
            if entry["type"] == "saved":
                row     = entry["data"]
                test_id = int(row["test_id"])
                payload = load_run_payload(test_id)
                if payload is not None:
                    render_dashboard(payload, show_save=False, key_prefix=f"run_{test_id}")
                else:
                    ann = row["annualized_return_pct"]
                    pf  = row["profit_factor"]
                    dd  = row["max_drawdown_pct"]
                    wr  = row["win_rate"]
                    _, gl, gc = grade_info(ann if pd.notna(ann) else None)
                    st.markdown(
                        f'<span class="grade-badge" style="background:{gc}22;color:{gc};'
                        f'border:1px solid {gc}66;font-size:1rem;">{gl}</span>',
                        unsafe_allow_html=True
                    )
                    st.caption(f"{row['stream_name']} {row['stream_version']}  ·  "
                               f"{str(row['test_start'])[:10]} → {str(row['test_end'])[:10]}")
                    st.divider()
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Annualized Return", f"{ann:+.1f}%" if pd.notna(ann) else "—")
                    c2.metric("Profit Factor",     f"{pf:.2f}"    if pd.notna(pf)  else "—")
                    c3.metric("Max Drawdown",      f"{dd:.1f}%"   if pd.notna(dd)  else "—")
                    c4.metric("Win Rate",          f"{wr*100:.0f}%" if pd.notna(wr) else "—")
                    st.info("Full charts not available — saved before per-test pkl storage. "
                            "Re-run from Claude Code to restore charts.")
                    if row.get("notes"):
                        st.caption(f"💬 {row['notes']}")

            else:  # pending — unsaved run with full dashboard + save button
                pr      = entry["data"]
                payload = pr["payload"]
                pkey    = f"pending_{pr['start']}_{pr['end']}"
                render_dashboard(payload, show_save=True, key_prefix=pkey)
                # Delete the pending file after successful save (handled inside render_dashboard
                # via st.rerun; pending file cleaned up on next load since it's now in saved_windows)

else:
    st.info("Waiting for a run from Claude Code. Results will appear here automatically.")
