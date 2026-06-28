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

LAST_RUN_PATH = Path(__file__).parent / ".last_run.pkl"

SP500_ANNUALIZED = 10.0  # benchmark baseline


def get_engine():
    return create_engine("postgresql+psycopg2://localhost/forge_anchor")


def candle_hours(params: dict) -> float:
    return {"1h": 1.0, "4h": 4.0, "1d": 24.0}.get(params.get("primary_timeframe"), 0.25)


def grade_info(ann):
    if ann is None:  return None, "No grade", "#555555"
    if ann >= 20:    return 5,    "Grade 5 · Elite",   "#00d4aa"
    if ann >= 10:    return 4,    "Grade 4 · Strong",  "#4ade80"
    if ann >= 8:     return 3,    "Grade 3 · Passing", "#facc15"
    if ann > 0:      return 2,    "Grade 2 · Weak",    "#fb923c"
    return           1,           "Grade 1 · Poor",    "#f87171"


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


def _compact_config(params: dict) -> str:
    """One-line config summary for the sidebar."""
    core   = params.get("core_signal", "")
    core_p = params.get("core_params") or {}
    pos    = params.get("position") or {}
    tf     = params.get("primary_timeframe") or "15m"
    filters = params.get("filters") or {}
    sentiment = params.get("sentiment") or {}

    if core == "ema_crossover":
        sig = f"{core_p.get('ema_short')}/{core_p.get('ema_long')} EMA"
    elif core == "rsi_dip":
        sig = f"RSI dip <{core_p.get('rsi_threshold')}"
    elif core == "range_breakout":
        sig = f"{core_p.get('breakout_lookback')}-candle breakout"
    elif core == "volume_surge":
        sig = f"{core_p.get('volume_multiplier')}× volume surge"
    elif core == "sma_pullback":
        sig = f"pullback to {core_p.get('pullback_sma')} SMA"
    else:
        sig = core

    parts = [f"{sig} · {tf}"]

    active_filters = []
    tc = filters.get("trend_context")
    if tc:
        active_filters.append(f">{tc.get('sma_period')} SMA")
    rsi_f = filters.get("rsi")
    if rsi_f:
        if rsi_f.get("min") is not None: active_filters.append(f"RSI>{rsi_f['min']}")
        if rsi_f.get("max") is not None: active_filters.append(f"RSI<{rsi_f['max']}")
    if filters.get("atr_regime"):
        active_filters.append("low-vol")
    fg = (sentiment.get("fear_greed") or {})
    if fg.get("max") is not None: active_filters.append(f"F&G<{fg['max']}")
    if fg.get("min") is not None: active_filters.append(f"F&G>{fg['min']}")
    if active_filters:
        parts.append(" · ".join(active_filters))

    trail = pos.get("trailing_stop_pct")
    if trail:
        parts.append(f"{trail}% trail")

    return "  ·  ".join(parts)


def _human_readable_description(params: dict) -> str:
    """Full English explanation of what a config does."""
    core      = params.get("core_signal", "")
    core_p    = params.get("core_params") or {}
    filters   = params.get("filters") or {}
    pos       = params.get("position") or {}
    sentiment = params.get("sentiment") or {}
    tf        = params.get("primary_timeframe")

    tf_desc = {
        "1h":  "hourly candles (4× less frequent than the raw 15-minute data — much less noise)",
        "4h":  "4-hour candles (very selective — only catches larger, more sustained moves)",
        "1d":  "daily candles (only major trend-level shifts trigger an entry)",
    }.get(tf, "15-minute candles (maximum granularity — every signal the data can produce)")

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
            f"Enters when RSI drops below {thresh} (oversold territory) and price is at least {dip}% "
            f"below its {smap}-period moving average — looking for genuine panic dips likely to bounce. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "range_breakout":
        lb  = core_p.get("breakout_lookback", 48)
        hrs = lb * 0.25
        core_sent = (
            f"Enters when price breaks above its highest point over the last {lb} candles ({hrs:.0f} hours) — "
            f"the moment a consolidation range gives way and a new move begins. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "volume_surge":
        mult = core_p.get("volume_multiplier", 2.5)
        core_sent = (
            f"Enters on a volume spike of {mult}× the recent average combined with a bullish candle — "
            f"a signal that unusually high participation is driving the move upward. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "sma_pullback":
        psma = core_p.get("pullback_sma", 50)
        tol  = core_p.get("pullback_tolerance_pct", 1.5)
        core_sent = (
            f"Enters when price pulls back to within {tol}% of the {psma}-period SMA during an uptrend — "
            f"buying a healthy dip in an established trend rather than chasing momentum. "
            f"Evaluated on {tf_desc}."
        )
    else:
        core_sent = f"Core signal: {core}. Evaluated on {tf_desc}."

    filter_sents = []
    tc = filters.get("trend_context")
    if tc:
        sma_p = tc.get("sma_period", 200)
        req   = "above" if tc.get("require", "above") == "above" else "below"
        filter_sents.append(
            f"Only enters when BTC price is {req} its {sma_p}-period SMA — "
            f"ensuring the trade aligns with the long-term trend direction."
        )
    rsi_f = filters.get("rsi")
    if rsi_f:
        rsi_parts = []
        if rsi_f.get("min") is not None:
            rsi_parts.append(f"above {rsi_f['min']} (momentum is present)")
        if rsi_f.get("max") is not None:
            rsi_parts.append(f"below {rsi_f['max']} (not yet overbought)")
        if rsi_parts:
            filter_sents.append(f"RSI must be {' and '.join(rsi_parts)} at the time of entry.")
    atr_f = filters.get("atr_regime")
    if atr_f:
        max_pct = atr_f.get("max_pct_of_avg", 70)
        filter_sents.append(
            f"Requires a calm market before entry — volatility (ATR) must be below {max_pct}% of its recent average, "
            f"so breakouts only happen after genuine consolidation, not during chaotic chop."
        )
    vol_f = filters.get("volume")
    if vol_f and filters.get("trend_context") is None:
        filter_sents.append(
            f"Volume must exceed {vol_f.get('min_multiplier')}× its recent average — "
            f"filtering out low-conviction moves."
        )

    fg = (sentiment.get("fear_greed") or {})
    if fg.get("max") is not None:
        filter_sents.append(
            f"The Fear & Greed Index must be below {fg['max']} at entry — "
            f"only trading during fearful or neutral market sentiment, not euphoria."
        )
    if fg.get("min") is not None:
        filter_sents.append(
            f"The Fear & Greed Index must be above {fg['min']} at entry — "
            f"only trading when sentiment is positive enough to support momentum."
        )

    trail    = pos.get("trailing_stop_pct")
    min_hold = pos.get("min_hold_candles")
    max_hold = pos.get("max_hold_candles")

    exit_sents = []
    if trail:
        exit_sents.append(
            f"Exits via a {trail}% trailing stop — the stop follows price upward and only triggers "
            f"if BTC drops {trail}% from its highest point since entry, letting winners run while capping losses."
        )
    if min_hold:
        exit_sents.append(f"Holds for at least {min_hold} candles before the stop can fire.")
    if max_hold:
        exit_sents.append(f"Force-exits after {max_hold} candles regardless of the trailing stop.")

    return " ".join([core_sent] + filter_sents + exit_sents)


# ── Page config & CSS ──────────────────────────────────────────────────────────

st.set_page_config(page_title="Stream Tester", layout="wide", page_icon="⚓")

st.markdown("""
<style>
/* Metric card backgrounds */
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 18px 20px 14px 20px;
}
/* Tighten sidebar padding */
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
/* Grade badge */
.grade-badge {
    display: inline-block;
    padding: 6px 18px;
    border-radius: 20px;
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    margin-bottom: 4px;
}
/* Section divider label */
.section-label {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #888;
    margin: 0 0 8px 0;
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
        "Can be used as an entry gate — e.g. only enter when F&G < 35 confirms genuine panic.",
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

# ── Sidebar — Run History ──────────────────────────────────────────────────────

with st.sidebar:
    st.header("Run History")
    st.caption("Triggered from Claude Code · Save to record here")

    try:
        engine = get_engine()
        with engine.connect() as conn:
            history = pd.read_sql(text("""
                SELECT test_id, stream_name, stream_version, parameters,
                       total_trades, profit_factor, annualized_return_pct,
                       max_drawdown_pct, avg_winner_pct, avg_loser_pct,
                       win_rate, saved_at, notes
                FROM backtest.stream_tests
                ORDER BY saved_at ASC
            """), conn)
    except Exception:
        history = pd.DataFrame()

    if history.empty:
        st.info("No saved runs yet.")
    else:
        total = len(history)
        for attempt_num, (_, row) in enumerate(history.iterrows(), start=1):
            try:
                p = row["parameters"] if isinstance(row["parameters"], dict) \
                    else json.loads(row["parameters"])
                compact  = _compact_config(p)
                readable = _human_readable_description(p)
            except Exception:
                compact  = "—"
                readable = "—"

            pf   = f"{row['profit_factor']:.2f}"         if pd.notna(row["profit_factor"])         else "—"
            ann  = f"{row['annualized_return_pct']:+.1f}%" if pd.notna(row["annualized_return_pct"]) else "—"
            dd   = f"{row['max_drawdown_pct']:.1f}%"     if pd.notna(row["max_drawdown_pct"])       else "—"
            wr   = f"{row['win_rate']*100:.0f}%"         if pd.notna(row["win_rate"])               else "—"
            _, grade_label, grade_color = grade_info(
                row["annualized_return_pct"] if pd.notna(row["annualized_return_pct"]) else None
            )

            st.markdown(
                f'<span class="grade-badge" style="background:{grade_color}20;'
                f'color:{grade_color};border:1px solid {grade_color}55;">'
                f'#{attempt_num} &nbsp;{grade_label}</span>',
                unsafe_allow_html=True
            )
            st.markdown(f"**{row['stream_name']} {row['stream_version']}**")
            st.caption(compact)
            st.markdown(f"*{readable}*")
            st.caption(
                f"PF {pf}  ·  Ann {ann}  ·  DD {dd}  ·  WR {wr}  ·  {row['total_trades']} trades"
            )
            if row["notes"]:
                st.caption(f"💬 {row['notes']}")
            st.divider()


# ── Main — load last run ───────────────────────────────────────────────────────

payload = None
if LAST_RUN_PATH.exists():
    try:
        with open(LAST_RUN_PATH, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        payload = None

if not payload:
    st.info("Waiting for a run from Claude Code. Results appear here automatically.")
    st.stop()

result          = payload["result"]
trades          = payload["trades"]
df              = payload["df"]
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
    st.stop()

closed = trades[trades["exit_reason"] != "partial"].sort_values("exit_ts").copy()
closed["return_pct"] = (closed["exit_price"] - closed["entry_price"]) / closed["entry_price"] * 100

# ── Header ─────────────────────────────────────────────────────────────────────

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

# ── Hero KPIs ──────────────────────────────────────────────────────────────────

st.markdown('<p class="section-label">Performance</p>', unsafe_allow_html=True)

h1, h2, h3, h4 = st.columns(4)

h1.metric(
    "Ending Balance",
    f"${ending_capital:.2f}",
    delta=f"${ending_capital - initial_capital:+.2f} total P&L",
    help=f"Started at ${initial_capital:.2f}. All trades and fees included."
)

h2.metric(
    "Annualized Return",
    f"{ann:+.1f}%" if ann is not None else "—",
    delta=f"{ann - SP500_ANNUALIZED:+.1f}% vs S&P 500" if ann is not None else None,
    delta_color="normal" if ann is not None and ann >= SP500_ANNUALIZED else "inverse",
    help=f"Compounded yearly return. S&P 500 benchmark: ~{SP500_ANNUALIZED:.0f}%."
)

bh_ann = bh.get("annualized_return_pct")
vs_bh  = (ann - bh_ann) if (ann is not None and bh_ann is not None) else None
h3.metric(
    "vs BTC Buy & Hold",
    f"{bh_ann:+.1f}% BTC" if bh_ann is not None else "—",
    delta=f"{vs_bh:+.1f}% difference" if vs_bh is not None else None,
    delta_color="normal" if vs_bh is not None and vs_bh >= 0 else "inverse",
    help="BTC buy-and-hold return for the same period. Positive delta = this strategy beat HODL."
)

h4.metric(
    "Total Return",
    f"{metrics['total_return_pct']:+.1f}%",
    delta=f"{period_str}",
    delta_color="off",
    help="Cumulative return over the full backtest period (not annualized)."
)

st.divider()

# ── Stats row ──────────────────────────────────────────────────────────────────

st.markdown('<p class="section-label">Trade Statistics</p>', unsafe_allow_html=True)

s1, s2, s3, s4, s5, s6 = st.columns(6)

s1.metric(
    "Win Rate",
    f"{metrics['win_rate']*100:.1f}%" if metrics["win_rate"] else "—",
    help="% of closed trades that ended in profit. Win rate alone doesn't determine profitability — size of wins matters too."
)
s2.metric(
    "Profit Factor",
    str(metrics["profit_factor"]) if metrics["profit_factor"] else "—",
    help="Total $ won ÷ total $ lost. >1.0 = profitable. 1.5 = made $1.50 per $1 lost."
)
s3.metric(
    "Max Drawdown",
    f"{metrics['max_drawdown_pct']:.1f}%" if metrics["max_drawdown_pct"] is not None else "—",
    delta="worst peak-to-trough drop",
    delta_color="off",
    help="Largest drop from peak balance to lowest point. Lower (less negative) is better."
)
avg_hold_hrs = round(metrics["avg_hold_candles"] * c_hrs, 1) if metrics["avg_hold_candles"] else None
s4.metric(
    "Avg Hold",
    f"{avg_hold_hrs}h" if avg_hold_hrs else "—",
    help="Average time in a trade."
)
s5.metric(
    "Avg Winner",
    f"{metrics['avg_winner_pct']:+.1f}%" if metrics["avg_winner_pct"] else "—",
    help="Average % return on winning trades."
)
s6.metric(
    "Avg Loser",
    f"{metrics['avg_loser_pct']:.1f}%" if metrics["avg_loser_pct"] else "—",
    help="Average % loss on losing trades. Ideally smaller (in absolute terms) than avg winner."
)

st.divider()

# ── Equity curve ──────────────────────────────────────────────────────────────

equity = initial_capital + closed["pnl"].cumsum()

fig_eq = go.Figure()
fig_eq.add_trace(go.Scatter(
    x=closed["exit_ts"], y=equity,
    fill="tozeroy",
    fillcolor="rgba(0,212,170,0.07)",
    line=dict(color="#00d4aa", width=2.5),
    hovertemplate="<b>%{x|%b %d, %Y}</b><br>Balance: $%{y:.2f}<extra></extra>"
))
fig_eq.add_hline(
    y=initial_capital,
    line=dict(color="#555", dash="dot"),
    annotation_text=f"Start ${initial_capital:.0f}",
    annotation_font_color="#888"
)
if bh_ann is not None:
    bh_end = initial_capital * (1 + bh.get("total_return_pct", 0) / 100)
    fig_eq.add_annotation(
        x=closed["exit_ts"].iloc[-1], y=bh_end,
        text=f"BTC buy & hold: ${bh_end:.2f}",
        showarrow=False, font=dict(color="#f59e0b", size=11),
        xanchor="right"
    )
fig_eq.update_layout(
    template="plotly_dark",
    title="Account Balance Over Time",
    xaxis_title=None, yaxis_title="Balance ($)",
    height=360, margin=dict(t=40, b=20, l=10, r=10),
    showlegend=False,
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(fig_eq, use_container_width=True)

col_dd, col_dist = st.columns(2)

# ── Drawdown ──────────────────────────────────────────────────────────────────
with col_dd:
    peak     = equity.cummax()
    drawdown = (equity - peak) / peak * 100
    worst    = drawdown.min()

    fig_dd = go.Figure(go.Scatter(
        x=closed["exit_ts"], y=drawdown,
        fill="tozeroy",
        line=dict(color="#f87171", width=1.5),
        fillcolor="rgba(248,113,113,0.12)",
        hovertemplate="<b>%{x|%b %d, %Y}</b><br>%{y:.1f}% below peak<extra></extra>"
    ))
    fig_dd.update_layout(
        template="plotly_dark",
        title=f"Drawdown  (worst: {worst:.1f}%)",
        xaxis_title=None, yaxis_title="% below peak",
        height=280, margin=dict(t=40, b=20, l=10, r=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_dd, use_container_width=True)
    st.caption(f"0% = at all-time high. Worst drop: **{worst:.1f}%** before recovering. Closer to 0 is better.")

# ── Return distribution ───────────────────────────────────────────────────────
with col_dist:
    winners = closed[closed["return_pct"] > 0]["return_pct"]
    losers  = closed[closed["return_pct"] <= 0]["return_pct"]

    fig_hist = go.Figure()
    fig_hist.add_trace(go.Histogram(
        x=winners, name="Winners",
        marker_color="#00d4aa", opacity=0.8, xbins=dict(size=0.5)
    ))
    fig_hist.add_trace(go.Histogram(
        x=losers, name="Losers",
        marker_color="#f87171", opacity=0.8, xbins=dict(size=0.5)
    ))
    fig_hist.update_layout(
        template="plotly_dark",
        title="Win / Loss Distribution",
        xaxis_title="Return per trade (%)", yaxis_title="# trades",
        barmode="overlay", height=280,
        margin=dict(t=40, b=20, l=10, r=10),
        legend=dict(x=0.75, y=0.99),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_hist, use_container_width=True)
    st.caption(
        f"Green = winning trades, red = losing trades. "
        f"Avg winner: **{metrics['avg_winner_pct']:+.1f}%** vs avg loser: **{metrics['avg_loser_pct']:.1f}%**."
        if metrics["avg_winner_pct"] and metrics["avg_loser_pct"] else
        "Distribution of per-trade returns."
    )

# ── Trade log ─────────────────────────────────────────────────────────────────
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
        "start_value": "$ In", "end_value": "$ Out", "gain_loss": "P&L ($)",
        "return_pct": "Return %", "duration_hrs": "Hours Held", "exit_reason": "Exit Reason",
    })
    st.dataframe(display, use_container_width=True)

# ── Save ──────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("💾 Save This Run")
save_notes = st.text_input(
    "Notes (optional)",
    placeholder="e.g. switched to 1h candles — profit factor flipped positive, drawdown still heavy"
)
if st.button("Save to Database", type="secondary"):
    try:
        save_stream_test(
            stream_name=display_name, params=params, result=result,
            metrics=metrics, initial_capital=initial_capital,
            ending_balance=ending_capital, notes=save_notes,
        )
        st.success(
            f"Saved — **{display_name}**, {metrics['total_trades']} trades, "
            f"{metrics['annualized_return_pct']:+.1f}% annualized, ${ending_capital:.2f} ending balance."
        )
        st.rerun()
    except Exception as e:
        st.error(f"Save failed: {e}")
