import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from src.app.db import (
    load_models, load_dashboard_lots, load_dashboard_model_tests,
    load_current_btc_price, load_blended_starting_capital,
)
from src.app.model_dashboard import STREAM_COLORS, DEFAULT_COLORS
from src.fees import TAKER_FEE

st.set_page_config(page_title="Model Dashboard", layout="wide")


# ── helpers ───────────────────────────────────────────────────────────────────

def _color(name, idx=0):
    return STREAM_COLORS.get(name, DEFAULT_COLORS[idx % len(DEFAULT_COLORS)])


def _pct(pnl, capital):
    return pnl / capital * 100 if capital else 0.0


def _ann(pnl, capital, days):
    if not capital or not days or days < 1:
        return None
    years = days / 365.25
    total_return = pnl / capital
    return ((1 + total_return) ** (1 / years) - 1) * 100


def _wr(series):
    s = series.dropna()
    return (s > 0).sum() / len(s) if len(s) else None


def _render_blended_dashboard_log(closed_df: pd.DataFrame, key_prefix: str):
    """
    Hierarchical view for blended-average positions (Model 3's slot_mode),
    mirroring the "Blend N" concept from Stream Tester's _render_blended_trade_log
    -- one expander per closed position with the rolled-up cost basis / P&L,
    and (when the underlying source actually has it) the individual fills
    nested inside.

    Real per-fill detail only exists for LIVE data (live.blended_fills) --
    backtest.lots only ever persisted the rolled-up blend, not each fill, so
    those rows show the same summary metrics without the nested fills table.
    P&L/closing capital are trusted as-is from the DB rather than
    recomputing fees here, since backtest (maker fee both sides) and live
    (taker fee on exit) use different fee models -- see
    blended_position_monitor.py's fee docstring.
    """
    ordered = closed_df.sort_values("opened_at").reset_index(drop=True)
    for i, row in ordered.iterrows():
        blend_num   = i + 1
        capital_in  = float(row["opening_capital"])
        capital_out = float(row["closing_capital"]) if pd.notna(row["closing_capital"]) else capital_in + float(row["realized_pnl"])
        pnl         = float(row["realized_pnl"])
        return_pct  = pnl / capital_in * 100 if capital_in else 0.0
        result_word = "WIN" if pnl > 0.001 else ("FLAT" if abs(pnl) <= 0.001 else "LOSS")
        entry_price = float(row["entry_price"])
        exit_price  = float(row["exit_price"]) if pd.notna(row["exit_price"]) else None
        total_qty   = capital_in / entry_price if entry_price else 0.0

        has_fills = isinstance(row.get("fill_prices"), (list, tuple)) and len(row["fill_prices"]) > 0
        num_fills = len(row["fill_prices"]) if has_fills else None
        slot1_capital = row["fill_capitals"][0] if has_fills else None

        slots_note = f"{num_fills} slot{'s' if num_fills != 1 else ''} @ ${slot1_capital:,.2f}/slot  ·  " if has_fills else ""
        header = (
            f"Blend {blend_num}  —  {result_word}  ·  {slots_note}"
            f"${capital_in:,.2f} → ${capital_out:,.2f}  ({return_pct:+.2f}%)  ·  "
            f"{row['opened_at']:%b %d, %Y} → {row['closed_at']:%b %d, %Y}"
        )
        with st.expander(header, expanded=False):
            r1c1, r1c2, r1c3, r1c4 = st.columns(4)
            r1c1.metric("Starting Capital", f"${capital_in:,.2f}")
            r1c2.metric("Ending Capital", f"${capital_out:,.2f}", delta=f"${pnl:,.2f}")
            r1c3.metric("Opened", f"{row['opened_at']:%b %d, %Y}")
            r1c4.metric("Closed", f"{row['closed_at']:%b %d, %Y}")

            r2c1, r2c2, r2c3 = st.columns(3)
            r2c1.metric("Total BTC Bought", f"{total_qty:.6f}")
            r2c2.metric("Blended Avg Cost", f"${entry_price:,.2f}")
            r2c3.metric("Exit Price", f"${exit_price:,.2f}" if exit_price else "—")

            if has_fills:
                fills_df = pd.DataFrame({
                    "Slot":       [f"Slot {j+1}" for j in range(num_fills)],
                    "Filled":     [f"{ts:%b %d, %Y %H:%M}" for ts in row["fill_timestamps"]],
                    "Price":      [f"${p:,.2f}" for p in row["fill_prices"]],
                    "Capital":    [f"${c:,.2f}" for c in row["fill_capitals"]],
                    "BTC Bought": [f"{q:.6f}" for q in row["fill_qtys"]],
                })
                st.dataframe(fills_df, use_container_width=True, hide_index=True, key=f"{key_prefix}_blend_{blend_num}_fills")
            else:
                st.caption("Per-fill detail isn't available for backtest history -- only the rolled-up blend was saved. Live trade history shows every fill.")


def _delta_color(val):
    return "normal" if val >= 0 else "inverse"


def _render_slot_status(open_pos: pd.DataFrame, closed: pd.DataFrame, unrealized_fn, btc_price, today):
    """
    Replaces the per-stream "Stream Status" cards for a blended model -- a
    solo stream with N weighted cascade slots isn't "streams," it's slots,
    so this shows the current ladder position and historical stats about
    how deep positions typically go, which the per-stream view had no way
    to express at all.
    """
    slot_total = 5
    if not closed.empty:
        slot_total = int(closed["slot_count"].iloc[0])
    elif not open_pos.empty:
        slot_total = int(open_pos["slot_count"].iloc[0])

    st.markdown("**Current Position**")
    if open_pos.empty:
        st.info("No position open -- waiting for the next entry signal.")
    else:
        op = open_pos.iloc[0]
        slots_filled = int(op["slot_number"])
        deployed     = float(op["opening_capital"])
        pool         = float(op["capital_base"]) if pd.notna(op.get("capital_base")) else None
        avg_cost     = float(op["entry_price"])
        unreal       = unrealized_fn(op)
        unreal_pct   = unreal / deployed * 100 if deployed else 0.0
        hwm          = float(op["high_water_mark"]) if pd.notna(op.get("high_water_mark")) else avg_cost
        eff_hwm      = max(hwm, btc_price) if btc_price else hwm
        trail_pct    = op.get("trailing_stop_pct")
        arm_pct      = op.get("trail_arm_gain_pct")
        gain_pct     = (eff_hwm - avg_cost) / avg_cost * 100 if avg_cost else 0.0
        armed        = not arm_pct or gain_pct >= arm_pct
        # Never active below the arm threshold -- a naive HWM*(1-trail%) number
        # here would imply a live stop that isn't actually there yet. Once
        # armed, floor it at breakeven so it never reads as a voluntary-loss
        # price (matches place_exit's real formula, see blended_order_manager.py).
        trail_stop   = max(eff_hwm * (1 - trail_pct / 100), avg_cost / (1 - TAKER_FEE)) if trail_pct and armed else None
        days_open    = (today - pd.Timestamp(op["opened_at"].date())).days

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Slots Filled", f"{slots_filled} / {slot_total}")
        c2.metric("Capital Deployed", f"${deployed:,.2f}")
        c3.metric("Blended Avg Cost", f"${avg_cost:,.2f}")
        c4.metric("Days Open", str(days_open))
        if pool:
            c2.caption(f"of ${pool:,.2f} pool")

        c5, c6, c7 = st.columns(3)
        sign = "+" if unreal >= 0 else ""
        c5.metric("Unrealized P&L", f"{sign}${unreal:,.2f}", delta=f"{sign}{unreal_pct:.1f}%", delta_color=_delta_color(unreal))
        c6.metric("BTC Now", f"${btc_price:,.2f}" if btc_price else "—")
        if trail_stop:
            c7.metric("Trail Stop", f"${trail_stop:,.2f}")
        elif arm_pct:
            c7.metric("Trail Stop", "Not armed")
            c7.caption(f"needs +{arm_pct}% (at +{gain_pct:.1f}%)")
        else:
            c7.metric("Trail Stop", "—")

        if slots_filled >= slot_total:
            st.warning("⚠️ All slots filled — capitulation stop is now armed (no more room to average down).")
        else:
            remaining = slot_total - slots_filled
            st.caption(f"{remaining} slot{'s' if remaining != 1 else ''} remaining in the ladder before capitulation risk is armed.")

    st.divider()
    st.markdown("**Slot Depth — Historical**")
    if closed.empty:
        st.caption("No closed positions yet.")
        return

    depth_rows = []
    for n in range(1, slot_total + 1):
        subset = closed[closed["slot_number"] == n]
        if subset.empty:
            continue
        wr = _wr(subset["realized_pnl"])
        avg_return_pct = (subset["realized_pnl"] / subset["opening_capital"] * 100).mean()
        hold_days = ((subset["closed_at"] - subset["opened_at"]).dt.total_seconds() / 86400).mean()
        depth_rows.append({
            "Slots Used": n,
            "Positions": len(subset),
            "% of All": f"{len(subset) / len(closed) * 100:.0f}%",
            "Win Rate": f"{wr * 100:.0f}%" if wr is not None else "—",
            "Avg Return": f"{avg_return_pct:+.2f}%",
            "Avg P&L": f"${subset['realized_pnl'].mean():+.2f}",
            "Avg Hold": f"{hold_days:.1f}d",
        })
    depth_df = pd.DataFrame(depth_rows)

    dcol, chartcol = st.columns([3, 2])
    with dcol:
        st.dataframe(depth_df, use_container_width=True, hide_index=True)
    with chartcol:
        fig = go.Figure(go.Bar(
            x=depth_df["Slots Used"], y=depth_df["Positions"],
            marker_color="#60a5fa",
            hovertemplate="%{x} slot(s): %{y} positions<extra></extra>",
        ))
        fig.update_layout(
            height=220, margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(title="Slots Used", showgrid=False, dtick=1),
            yaxis=dict(title="Positions", showgrid=True, gridcolor="rgba(255,255,255,0.07)"),
        )
        st.plotly_chart(fig, use_container_width=True, key="slot_depth_dist")

    avg_slots = closed["slot_number"].mean()
    pct_maxed = (closed["slot_number"] == slot_total).mean() * 100
    exit_counts = closed["exit_reason"].value_counts()
    trail_stops = int(exit_counts.get("trailing_stop", 0))
    cap_stops   = int(exit_counts.get("capitulation_stop", 0))

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Avg Slots Used", f"{avg_slots:.1f} / {slot_total}")
    k2.metric("Fully Maxed Out", f"{pct_maxed:.0f}%", help="Share of closed positions that used every slot before exiting")
    k3.metric("Trailing Stop Exits", f"{trail_stops} ({trail_stops / len(closed) * 100:.0f}%)")
    k4.metric("Capitulation Exits", f"{cap_stops} ({cap_stops / len(closed) * 100:.0f}%)", help="Forced exit after every slot filled and price kept dropping")


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Settings")
    models = load_models()
    if not models:
        st.error("No models found.")
        st.stop()

    # model_id is backtest.models' internal serial PK -- model_version is the
    # actual "Model N" business label. They coincide for Models 1-2 but not
    # Model 3 (model_id=4, model_version=3), which is what surfaced this.
    model_opts = {f"Model {m['model_version']}": m["model_id"] for m in models}
    model_version_by_id = {m["model_id"]: m["model_version"] for m in models}
    model_labels = list(model_opts.keys())
    # Default to Model 1 -- the only model actually live as of 2026-08-11
    # (Model 3 was shut down and archived; see project_db_declutter memory).
    # Falls back to the last (most recent) model if Model 1 ever isn't listed.
    default_model_idx = model_labels.index("Model 1") if "Model 1" in model_labels else len(model_labels) - 1
    model_label = st.selectbox("Model", model_labels, index=default_model_idx)
    model_id = model_opts[model_label]
    model_version = model_version_by_id[model_id]

    source = st.radio("Data source", ["Backtest", "Live"], index=1, horizontal=True)

    model_test_id = None
    if source == "Backtest":
        tests_df = load_dashboard_model_tests(model_id)
        seeded = tests_df[tests_df["has_lots"] == True] if not tests_df.empty else pd.DataFrame()
        if seeded.empty:
            st.warning("No model tests with trade-level data. Re-run and save a model test.")
            st.stop()

        def _run_label(row):
            ann = f"{row['annualized_return_pct']:+.1f}%" if pd.notna(row["annualized_return_pct"]) else "—"
            if pd.notna(row.get("fee_maker_pct")) and pd.notna(row.get("fee_taker_pct")):
                fee_tag = f"fee {float(row['fee_maker_pct'])*100:.2f}%/{float(row['fee_taker_pct'])*100:.2f}%"
            else:
                fee_tag = "legacy — pre fee-tracking"
            return f"Run {int(row['run_number'])} · {row['timeframe_label']} · {ann} · {fee_tag}"

        seeded = seeded.copy()
        seeded["label"] = seeded.apply(_run_label, axis=1)
        run_opts = dict(zip(seeded["label"], seeded["model_test_id"]))
        run_labels = list(run_opts.keys())
        # Default to the most recent "Full History" run if one exists, else the first option.
        full_history_idx = [i for i, lbl in enumerate(run_labels) if "Full History" in lbl]
        default_idx = full_history_idx[-1] if full_history_idx else 0
        run_label = st.selectbox("Backtest run", run_labels, index=default_idx)
        model_test_id = run_opts[run_label]

    st.divider()
    if st.button("↻ Refresh"):
        st.cache_data.clear()
        st.rerun()


# ── load data ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load(model_id, source, model_test_id, model_version):
    return load_dashboard_lots(model_id, source.lower(), model_test_id, model_version)

lots = _load(model_id, source, model_test_id, model_version)
is_blended = bool(len(lots)) and (lots["slot_mode"] == "blended").any()

if lots.empty:
    st.info("No trade data found for this selection.")
    st.stop()

# Normalize timestamps: convert tz-aware Python datetimes → UTC-naive pandas Timestamps
for col in ("opened_at", "closed_at"):
    if col in lots.columns:
        lots[col] = pd.to_datetime(lots[col], utc=True).dt.tz_localize(None)

all_closed = lots[lots["status"] == "CLOSED"].copy()

# end_of_data trades are positions still open when the simulation ended — treat as open
if source == "Backtest":
    open_pos = all_closed[all_closed["exit_reason"] == "end_of_data"].copy()
    closed   = all_closed[all_closed["exit_reason"] != "end_of_data"].copy().sort_values("closed_at")
else:
    open_pos = lots[lots["status"] == "OPEN"].copy()
    closed   = all_closed.copy().sort_values("closed_at")

# Reference "today" and current BTC price
if source == "Backtest":
    today     = pd.Timestamp(all_closed["closed_at"].max().date())
    sim_start = pd.Timestamp(all_closed["opened_at"].min().date())
    btc_price = load_current_btc_price(source="local")
else:
    today     = pd.Timestamp(datetime.utcnow().date())
    sim_start = pd.Timestamp(closed["opened_at"].min().date()) if not closed.empty else today
    btc_price = load_current_btc_price(source="supabase")

ytd_start = pd.Timestamp(f"{today.year}-01-01")

# Capital: sum of opening_capital of each slot's first trade across all lots (incl. open)
first_per_slot = (
    all_closed.sort_values("opened_at")
    .drop_duplicates(subset=["full_stream_name", "slot_number"])
)
total_capital = float(first_per_slot["opening_capital"].sum())

if is_blended:
    # The generic calc above assumes a fixed lot size per slot -- wrong for a
    # compounding blended position, where opening_capital varies by how many
    # of the 5 slots actually filled before that position closed.
    blended_capital = load_blended_starting_capital(model_id, source.lower(), model_version)
    if blended_capital is not None:
        total_capital = blended_capital

# Unrealized P&L on currently open positions (using current BTC price)
def _unrealized(row):
    if btc_price is None:
        return 0.0
    return (btc_price - float(row["entry_price"])) / float(row["entry_price"]) * float(row["opening_capital"])

unrealized_pnl = float(open_pos.apply(_unrealized, axis=1).sum()) if not open_pos.empty else 0.0

# Running cumulative P&L (realized closed + unrealized open)
realized_pnl   = float(closed["realized_pnl"].sum())
total_pnl      = realized_pnl + unrealized_pnl
current_value  = total_capital + total_pnl

# YTD subset (closed trades only for realized; open pos counted separately)
ytd = closed[closed["closed_at"] >= ytd_start]
ytd_pnl = float(ytd["realized_pnl"].sum()) + unrealized_pnl

# Period helpers
all_days = max((today - sim_start).days, 1)
ytd_days = max((today - ytd_start).days, 1)

stream_names = sorted(lots["full_stream_name"].unique())


# ── page header ───────────────────────────────────────────────────────────────

st.title(f"📊 {model_label} — Portfolio Dashboard")
st.caption(f"{'Backtest simulation' if source == 'Backtest' else 'Live trading'} · as of {today.strftime('%b %d, %Y')}")

# ── portfolio snapshot metrics ────────────────────────────────────────────────

m1, m2, m3, m4, m5, m6 = st.columns(6)

m1.metric("Starting Capital",  f"${total_capital:.2f}")
m2.metric(
    "Current Value",
    f"${current_value:.2f}",
    delta=f"${total_pnl:+.2f}",
    delta_color=_delta_color(total_pnl),
)
m3.metric(
    "Total Return",
    f"{_pct(total_pnl, total_capital):+.1f}%",
    delta=f"${total_pnl:+.2f}",
    delta_color=_delta_color(total_pnl),
)
ann_all = _ann(total_pnl, total_capital, all_days)
m4.metric("Annualized", f"{ann_all:+.1f}%" if ann_all is not None else "—")

ytd_ann = _ann(ytd_pnl, total_capital, ytd_days)
m5.metric(
    f"YTD ({today.year})",
    f"${ytd_pnl:+.2f}",
    delta=f"{_pct(ytd_pnl, total_capital):+.1f}% · ann {ytd_ann:+.1f}%" if ytd_ann else None,
    delta_color=_delta_color(ytd_pnl),
)

wr_all = _wr(closed["realized_pnl"])
m6.metric("Win Rate", f"{wr_all*100:.1f}%" if wr_all else "—", delta=f"{len(closed)} trades")

st.divider()

# ── stream status / slot status ──────────────────────────────────────────────

if is_blended:
    st.subheader("Slot Status")
    _render_slot_status(open_pos, closed, _unrealized, btc_price, today)
else:
    st.subheader("Stream Status")

    cols = st.columns(len(stream_names))
    for col, (idx, sname) in zip(cols, enumerate(stream_names)):
        color = _color(sname, idx)
        s_closed = closed[closed["full_stream_name"] == sname]
        s_ytd    = ytd[ytd["full_stream_name"] == sname]
        s_open   = open_pos[open_pos["full_stream_name"] == sname] if not open_pos.empty else pd.DataFrame()

        s_pnl     = float(s_closed["realized_pnl"].sum())
        s_ytd_pnl = float(s_ytd["realized_pnl"].sum())
        s_wr      = _wr(s_closed["realized_pnl"])
        s_trades  = len(s_closed)

        with col:
            st.markdown(f"**<span style='color:{color}'>● {sname}</span>**", unsafe_allow_html=True)

            # Open position(s)
            if not s_open.empty:
                for _, op in s_open.iterrows():
                    days_open  = (today - pd.Timestamp(op["opened_at"].date())).days
                    ep         = float(op["entry_price"])
                    cap        = float(op["opening_capital"])
                    unreal     = _unrealized(op)
                    unreal_pct = unreal / cap * 100 if cap else 0.0
                    trail_pct  = op.get("trailing_stop_pct")
                    hwm        = float(op["high_water_mark"]) if pd.notna(op.get("high_water_mark")) else ep
                    eff_hwm    = max(hwm, btc_price) if btc_price else hwm
                    trail_stop = eff_hwm * (1 - trail_pct / 100) if trail_pct else None
                    sign       = "+" if unreal >= 0 else ""
                    col.write(f"🟢 Open {days_open}d")
                    col.caption(f"Entry: ${ep:,.0f}")
                    if btc_price:
                        col.caption(f"BTC now: ${btc_price:,.0f}")
                    col.caption(f"P&L: {sign}${unreal:.2f} ({sign}{unreal_pct:.1f}%)")
                    if trail_stop:
                        col.caption(f"Trail stop: ${trail_stop:,.0f}")
            else:
                col.caption("No open position")

            # Last closed trade
            if not s_closed.empty:
                last = s_closed.sort_values("closed_at").iloc[-1]
                days_ago = (today - pd.Timestamp(last["closed_at"].date())).days
                ago_str  = "today" if days_ago == 0 else f"{days_ago}d ago"
                sign     = "+" if last["realized_pnl"] >= 0 else ""
                col.caption(f"Last closed {ago_str}: {sign}${last['realized_pnl']:.2f} ({last['exit_reason']})")

            col.metric("All-time P&L", f"${s_pnl:+.2f}", delta=f"WR {s_wr*100:.0f}% · {s_trades} trades" if s_wr else None, delta_color=_delta_color(s_pnl))
            col.metric(f"YTD {today.year}", f"${s_ytd_pnl:+.2f}", delta=f"{len(s_ytd)} trades", delta_color=_delta_color(s_ytd_pnl))

st.divider()

# ── equity curve with period tabs ─────────────────────────────────────────────

st.subheader("Portfolio Growth")

period_tab, ytd_tab, d90_tab, d30_tab = st.tabs(["All Time", "YTD", "90 Days", "30 Days"])

def _equity_chart(df_slice, label):
    if df_slice.empty:
        st.info(f"No closed trades in this period.")
        return
    by_time = df_slice.sort_values("closed_at")

    fig = go.Figure()
    for idx, sname in enumerate(stream_names):
        s = by_time[by_time["full_stream_name"] == sname]
        if s.empty:
            continue
        color = _color(sname, idx)
        # Cumulative P&L within this slice only
        s = s.copy()
        s["cum"] = s["realized_pnl"].cumsum()
        fig.add_trace(go.Scatter(
            x=s["closed_at"], y=s["cum"], name=sname,
            line=dict(color=color, width=1.5), mode="lines",
            hovertemplate="%{x|%Y-%m-%d}<br>P&L: $%{y:.2f}<extra>" + sname + "</extra>",
        ))

    # Average line: at each trade timestamp, average the per-stream cumulative P&L
    # Build one cum-P&L series per stream, forward-fill to a shared timeline, then mean
    stream_series = {}
    for sname in stream_names:
        s = by_time[by_time["full_stream_name"] == sname]
        if not s.empty:
            cum = s.set_index("closed_at")["realized_pnl"].cumsum()
            stream_series[sname] = cum
    if stream_series:
        all_times = sorted(set(t for s in stream_series.values() for t in s.index))
        avg_vals = []
        for t in all_times:
            vals = [s.asof(t) for s in stream_series.values() if not pd.isna(s.asof(t))]
            avg_vals.append(sum(vals) / len(vals) if vals else None)
        fig.add_trace(go.Scatter(
            x=all_times, y=avg_vals, name="Avg Stream",
            line=dict(color="#888888", width=2.5, dash="dot"), mode="lines",
            hovertemplate="%{x|%Y-%m-%d}<br>Avg: $%{y:.2f}<extra>Avg Stream</extra>",
        ))
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.2)", line_width=1)
    fig.update_layout(
        height=340, margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", y=-0.25),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.07)", tickprefix="$"),
    )
    slice_pnl = float(df_slice["realized_pnl"].sum())
    slice_wr  = _wr(df_slice["realized_pnl"])
    st.plotly_chart(fig, use_container_width=True, key=f"equity_chart_{label}")
    c1, c2, c3 = st.columns(3)
    c1.metric(f"{label} P&L", f"${slice_pnl:+.2f}", delta=f"{_pct(slice_pnl, total_capital):+.1f}% of capital", delta_color=_delta_color(slice_pnl))
    c2.metric("Trades", str(len(df_slice)))
    c3.metric("Win Rate", f"{slice_wr*100:.1f}%" if slice_wr else "—")

with period_tab:
    _equity_chart(closed, "All-time")
with ytd_tab:
    _equity_chart(closed[closed["closed_at"] >= ytd_start], f"YTD {today.year}")
with d90_tab:
    _equity_chart(closed[closed["closed_at"] >= today - pd.Timedelta(days=90)], "90-day")
with d30_tab:
    _equity_chart(closed[closed["closed_at"] >= today - pd.Timedelta(days=30)], "30-day")

st.divider()

# ── monthly P&L breakdown ─────────────────────────────────────────────────────

st.subheader("Monthly P&L")

if not closed.empty:
    monthly = (
        closed.copy()
        .assign(month=lambda df: df["closed_at"].dt.to_period("M"))
        .groupby("month")["realized_pnl"].sum()
        .reset_index()
        .rename(columns={"realized_pnl": "P&L"})
        .sort_values("month", ascending=False)
    )
    monthly["Month"] = monthly["month"].dt.strftime("%b %Y")
    monthly["P&L $"] = monthly["P&L"].map(lambda x: f"${x:+.2f}")
    monthly["Return %"] = monthly["P&L"].map(lambda x: f"{_pct(x, total_capital):+.2f}%")
    monthly["vs Capital"] = monthly["P&L"].map(
        lambda x: "🟢" if x > 0 else ("🔴" if x < 0 else "⚪")
    )

    # Show recent 24 months max
    display_monthly = monthly.head(24)[["vs Capital", "Month", "P&L $", "Return %"]]
    st.dataframe(display_monthly, use_container_width=True, hide_index=True, height=min(400, (len(display_monthly) + 1) * 35 + 10))

st.divider()

# ── open positions ────────────────────────────────────────────────────────────

st.subheader("Open Positions")
if source == "Backtest":
    st.caption("Showing positions still open at end of simulation (end_of_data exit). Values use current BTC price.")

if not open_pos.empty:
    rows = []
    for _, op in open_pos.iterrows():
        days_open  = (today - pd.Timestamp(op["opened_at"].date())).days
        ep         = float(op["entry_price"])
        cap        = float(op["opening_capital"])
        unreal     = _unrealized(op)
        unreal_pct = unreal / cap * 100 if cap else 0.0
        hwm        = float(op["high_water_mark"]) if pd.notna(op.get("high_water_mark")) else ep
        trail_pct  = op.get("trailing_stop_pct")
        arm_pct    = op.get("trail_arm_gain_pct")
        eff_hwm    = max(hwm, btc_price) if btc_price else hwm
        armed      = not arm_pct or (eff_hwm - ep) / ep * 100 >= arm_pct if ep else True
        # Blended (Model 3) positions have an arm gate + breakeven floor that
        # single-lot positions (Model 1) don't -- see _render_slot_status above
        # for the full explanation. arm_pct is only ever set for blended rows.
        trail_stop = (max(eff_hwm * (1 - trail_pct / 100), ep / (1 - TAKER_FEE)) if arm_pct
                      else eff_hwm * (1 - trail_pct / 100)) if trail_pct and armed else None
        rows.append({
            "Stream":          op["full_stream_name"],
            "Slot":            int(op["slot_number"]),
            "Opened":          op["opened_at"].strftime("%Y-%m-%d"),
            "Days Open":       days_open,
            "Entry $":         f"${ep:,.2f}",
            "Capital":         f"${cap:.2f}",
            "BTC Now":         f"${btc_price:,.2f}" if btc_price else "—",
            "Unrealized P&L":  f"${unreal:+.2f}",
            "Unrealized %":    f"{unreal_pct:+.1f}%",
            "HWM":             f"${eff_hwm:,.0f}",
            "Trail Stop":      f"${trail_stop:,.0f}" if trail_stop else "—",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if btc_price:
        st.caption(f"BTC price: ${btc_price:,.2f} (latest candle from {'simulation data' if source == 'Backtest' else 'Supabase'})")
else:
    st.info("No open positions.")

# ── trade log ─────────────────────────────────────────────────────────────────

st.subheader("Trade Log")

stream_filter = st.multiselect("Filter by stream", stream_names, default=[])

log = closed.copy().sort_values("closed_at", ascending=False).reset_index(drop=True)
log.index = log.index + 1  # 1-based global trade number (most recent = 1)

if stream_filter:
    log = log[log["full_stream_name"].isin(stream_filter)]

if is_blended:
    _render_blended_dashboard_log(log, key_prefix="dash")
    st.caption(f"Showing {len(log)} of {len(closed)} blends")
else:
    log["return_pct"] = (log["realized_pnl"] / log["opening_capital"] * 100).round(2)
    log["hold_days"]  = ((log["closed_at"] - log["opened_at"]).dt.total_seconds() / 86400).round(1)

    display_log = log[[
        "full_stream_name", "slot_number",
        "opened_at", "closed_at", "hold_days",
        "entry_price", "exit_price",
        "opening_capital", "realized_pnl", "return_pct", "exit_reason",
    ]].rename(columns={
        "full_stream_name": "Stream", "slot_number": "Slot",
        "opened_at": "Opened", "closed_at": "Closed", "hold_days": "Days",
        "entry_price": "Entry $", "exit_price": "Exit $",
        "opening_capital": "Capital", "realized_pnl": "P&L $", "return_pct": "Return %",
        "exit_reason": "Exit Reason",
    })
    display_log["Opened"] = display_log["Opened"].dt.strftime("%Y-%m-%d")
    display_log["Closed"] = display_log["Closed"].dt.strftime("%Y-%m-%d")

    st.dataframe(
        display_log.style
            .format({
                "Entry $":   "${:,.0f}",
                "Exit $":    "${:,.0f}",
                "Capital":   "${:,.2f}",
                "P&L $":     "${:+.2f}",
                "Return %":  "{:+.2f}%",
                "Days":      "{:.1f}",
            })
            .map(
                lambda v: "color: #4ade80" if isinstance(v, (int, float)) and v > 0
                          else "color: #f87171" if isinstance(v, (int, float)) and v < 0
                          else "",
                subset=["P&L $", "Return %"],
            ),
        use_container_width=True,
        height=420,
    )
    st.caption(f"Showing {len(display_log)} of {len(closed)} trades · sorted by close date (most recent first)")
