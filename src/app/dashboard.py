"""
Dashboard renderer for a single stream backtest run.
"""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from .utils import SP500_HISTORICAL_AVG, fetch_sp500, candle_hours, grade_info, label_window
from .db import load_stream_history, load_timeframe_presets, save_stream_test


def _preset_save_ui(payload, result, display_name, params, metrics,
                    initial_capital, ending_capital, key_prefix):
    """Preset-based save UI — replaces free-text window name field."""
    presets = load_timeframe_presets()

    # Auto-detect which preset matches this run's actual simulation dates
    run_start = pd.Timestamp(result["start"]).date()
    run_end   = pd.Timestamp(result["end"]).date()

    def _matches(preset) -> bool:
        ps = preset["start_date"]
        pe = preset["end_date"]
        # start must be within 5 days (warmup clip can shift it slightly)
        if hasattr(ps, "date"):
            ps = ps
        else:
            ps = pd.Timestamp(ps).date()
        if abs((run_start - ps).days) > 5:
            return False
        # open-ended preset: run_end just needs to be past preset start
        if pe is None:
            return True
        pe = pe if not hasattr(pe, "date") else pe
        if isinstance(pe, str):
            pe = pd.Timestamp(pe).date()
        return abs((run_end - pe).days) <= 5

    matched_preset_id = None
    for p in presets:
        if _matches(p):
            matched_preset_id = p["preset_id"]
            break

    options       = [f"{p['name']}" for p in presets] + ["Custom"]
    default_idx   = next((i for i, p in enumerate(presets) if p["preset_id"] == matched_preset_id),
                         len(options) - 1)

    sc1, sc2 = st.columns([1, 2])
    selected_label = sc1.selectbox("Timeframe", options, index=default_idx,
                                   key=f"{key_prefix}_preset_sel")
    save_notes     = sc2.text_input("Notes (optional)", key=f"{key_prefix}_notes",
                                    placeholder="e.g. tighter stop improves by +0.8%")

    save_preset_id   = None
    save_custom_start = None
    save_custom_end   = None

    if selected_label == "Custom":
        d1, d2 = st.columns(2)
        save_custom_start = d1.date_input("Start date", value=run_start,
                                          key=f"{key_prefix}_cstart")
        open_ended = d2.checkbox("Open-ended (no end date)", key=f"{key_prefix}_open")
        if not open_ended:
            save_custom_end = d2.date_input("End date", value=run_end,
                                            key=f"{key_prefix}_cend")
    else:
        save_preset_id = next(p["preset_id"] for p in presets if p["name"] == selected_label)

    if st.button("Save to Database", type="secondary", key=f"{key_prefix}_save"):
        try:
            history_df = load_stream_history()
            test_id, run_num = save_stream_test(
                stream_name=display_name, params=params, result=result,
                metrics=metrics, initial_capital=initial_capital,
                ending_balance=ending_capital, payload=payload,
                preset_id=save_preset_id,
                custom_start=save_custom_start,
                custom_end=save_custom_end,
                notes=save_notes, history=history_df,
            )
            ann_pct = metrics["annualized_return_pct"]
            ann_str = f"{ann_pct:+.1f}% annualized" if ann_pct is not None else "no trades"
            timeframe_label = selected_label if selected_label != "Custom" else (
                f"{save_custom_start} → {save_custom_end or 'present'}"
            )
            st.success(
                f"Saved — Run #{run_num} · {timeframe_label} · **{display_name}** · "
                f"{metrics['total_trades']} trades · {ann_str}."
            )
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")


def render_dashboard(payload: dict, show_save: bool = True, key_prefix: str = "dash"):
    result          = payload["result"]
    trades          = payload["trades"]
    metrics         = payload["metrics"]
    params          = payload["params"]
    bh              = payload.get("bh", {})
    initial_capital = payload["initial_capital"]
    ending_capital  = payload["ending_balance"]
    display_name    = payload["stream_name"]
    slot_count      = payload.get("slot_count", 1)
    slot_mode       = payload.get("slot_mode", "single")
    period_str      = f"{result['start'].date()} → {result['end'].date()}"
    tf              = params.get("primary_timeframe") or "15m"
    c_hrs           = candle_hours(params)
    ann             = metrics["annualized_return_pct"]
    _, grade_label, grade_color = grade_info(ann)

    has_trades = not trades.empty and metrics["total_trades"] > 0

    if not has_trades:
        st.warning("No trades were generated with these settings.")

    if has_trades:
        closed = trades[trades["exit_reason"] != "partial"].sort_values("exit_ts").copy()
        closed["return_pct"] = (
            (closed["exit_price"] - closed["entry_price"]) / closed["entry_price"] * 100
        )

    # Header
    col_title, col_grade = st.columns([3, 1])
    with col_title:
        st.subheader(display_name)
        slot_desc = f"{slot_count} slot{'s' if slot_count > 1 else ''}" + (
            f" · {slot_mode}" if slot_mode != "single" else ""
        )
        st.caption(
            f"{period_str}  ·  {tf} candles  ·  {slot_desc}  ·  "
            f"{result['signals'].sum()} signals  ·  {metrics['total_trades']} trades"
        )
    with col_grade:
        st.markdown(
            f'<div style="text-align:right;margin-top:8px;">'
            f'<span class="grade-badge" style="background:{grade_color}22;'
            f'color:{grade_color};border:1px solid {grade_color}66;font-size:1rem;">'
            f'{grade_label}</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # Performance KPIs
    st.markdown('<p class="section-label">Performance</p>', unsafe_allow_html=True)
    sp500_actual = fetch_sp500(str(result["start"].date()), str(result["end"].date()))
    sp500_ann    = sp500_actual["annualized_return_pct"] if sp500_actual else None
    bh_ann       = bh.get("annualized_return_pct")

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Starting Balance", f"${initial_capital:.2f}",
              help=f"{slot_count} slot{'s' if slot_count > 1 else ''} · ${initial_capital/slot_count:.2f}/lot")
    h2.metric("Ending Balance", f"${ending_capital:.2f}",
              delta=f"{ending_capital - initial_capital:+.2f}", delta_color="normal")
    h3.metric(
        "Annualized Return",
        f"{ann:+.1f}%" if ann is not None else "—",
        delta=f"{ann - SP500_HISTORICAL_AVG:+.1f}% vs S&P avg" if ann is not None else None,
        delta_color="normal",
    )
    h4.metric(
        "Total Return",
        f"{metrics['total_return_pct']:+.1f}%",
        delta=f"{ann:+.1f}% / year" if ann is not None else None,
        delta_color="normal",
    )

    # Benchmarks
    st.markdown(
        '<p class="section-label" style="margin-top:16px;">Benchmarks</p>',
        unsafe_allow_html=True,
    )
    b1, b2, b3 = st.columns(3)
    b1.metric("S&P 500 historical avg", f"{SP500_HISTORICAL_AVG:.0f}% / year",
              delta=f"{ann - SP500_HISTORICAL_AVG:+.1f}% vs us" if ann is not None else None,
              delta_color="normal")
    b2.metric(f"S&P 500 actual ({period_str})",
              f"{sp500_ann:+.1f}% / year" if sp500_ann is not None else "—",
              delta=f"{ann - sp500_ann:+.1f}% vs us" if (ann is not None and sp500_ann is not None) else None,
              delta_color="normal")
    b3.metric("BTC buy & hold",
              f"{bh_ann:+.1f}% / year" if bh_ann is not None else "—",
              delta=f"{ann - bh_ann:+.1f}% vs us" if (ann is not None and bh_ann is not None) else None,
              delta_color="normal")

    st.divider()

    if not has_trades:
        if show_save:
            st.divider()
            st.subheader("💾 Save This Run")
            _preset_save_ui(payload, result, display_name, params, metrics,
                            initial_capital, ending_capital, key_prefix)
        return

    # Trade stats
    st.markdown('<p class="section-label">Trade Statistics</p>', unsafe_allow_html=True)
    s1, s2, s3, s4, s5, s6 = st.columns(6)
    n_wins   = len(closed[closed["pnl"] > 0])
    n_losses = len(closed[closed["pnl"] <= 0])
    s1.metric("Win Rate",
              f"{metrics['win_rate']*100:.1f}%" if metrics["win_rate"] else "—",
              delta=f"{n_wins} wins · {n_losses} losses", delta_color="off")
    s2.metric("Profit Factor",
              str(metrics["profit_factor"]) if metrics["profit_factor"] else "—")
    s3.metric("Max Drawdown",
              f"{metrics['max_drawdown_pct']:.1f}%" if metrics["max_drawdown_pct"] is not None else "—",
              delta="worst peak-to-trough", delta_color="off")
    avg_hold_hrs = round(metrics["avg_hold_candles"] * c_hrs, 1) if metrics["avg_hold_candles"] else None
    s4.metric("Avg Hold", f"{avg_hold_hrs}h" if avg_hold_hrs else "—")
    s5.metric("Avg Winner",
              f"{metrics['avg_winner_pct']:+.1f}%" if metrics["avg_winner_pct"] else "—")
    s6.metric("Avg Loser",
              f"{metrics['avg_loser_pct']:.1f}%" if metrics["avg_loser_pct"] else "—")

    # Equity curve
    equity     = initial_capital + closed["pnl"].cumsum()
    peak_val   = equity.max()
    peak_ts    = closed.loc[equity.idxmax(), "exit_ts"]
    low_val    = equity.min()
    low_ts     = closed.loc[equity.idxmin(), "exit_ts"]
    profitable = ending_capital >= initial_capital
    curve_color = "#00d4aa" if profitable else "#f87171"
    fill_color  = "rgba(0,212,170,0.07)" if profitable else "rgba(248,113,113,0.07)"

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=closed["exit_ts"], y=equity,
        fill="tozeroy", fillcolor=fill_color,
        line=dict(color=curve_color, width=2.5),
        hovertemplate="<b>%{x|%b %d, %Y}</b><br>Balance: $%{y:.2f}<extra></extra>",
    ))
    fig_eq.add_hline(y=initial_capital, line=dict(color="#555", dash="dot"),
                     annotation_text=f"Start ${initial_capital:.0f}",
                     annotation_font_color="#888")
    fig_eq.add_trace(go.Scatter(
        x=[peak_ts], y=[peak_val], mode="markers+text",
        marker=dict(color="#4ade80", size=10),
        text=[f"  <b>High ${peak_val:.2f}</b><br>  {peak_ts.strftime('%b %d, %Y')}"],
        textposition="middle right", textfont=dict(color="#4ade80", size=12),
        hovertemplate=f"Peak — {peak_ts.strftime('%b %d, %Y')} — ${peak_val:.2f}<extra></extra>",
        showlegend=False,
    ))
    fig_eq.add_trace(go.Scatter(
        x=[low_ts], y=[low_val], mode="markers+text",
        marker=dict(color="#f87171", size=10),
        text=[f"  <b>Low ${low_val:.2f}</b><br>  {low_ts.strftime('%b %d, %Y')}"],
        textposition="middle right", textfont=dict(color="#f87171", size=12),
        hovertemplate=f"Trough — {low_ts.strftime('%b %d, %Y')} — ${low_val:.2f}<extra></extra>",
        showlegend=False,
    ))
    if sp500_ann is not None:
        sp_end = initial_capital * (
            (1 + sp500_ann / 100) ** ((result["end"] - result["start"]).days / 365.25)
        )
        fig_eq.add_annotation(
            x=closed["exit_ts"].iloc[-1], y=sp_end,
            text=f"S&P 500 this period: ${sp_end:.2f}",
            showarrow=False, font=dict(color="#f59e0b", size=11), xanchor="right",
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
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>%{y:.1f}% below peak<extra></extra>",
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
        trade_seq = list(range(1, len(closed) + 1))
        colors    = ["#4ade80" if r > 0 else "#f87171" for r in closed["return_pct"]]
        hover     = [
            f"Trade #{i}<br>{row.exit_ts.strftime('%b %d, %Y')}<br>{row.return_pct:+.2f}%"
            for i, row in zip(trade_seq, closed.itertuples())
        ]
        fig_trades = go.Figure()
        fig_trades.add_hline(y=0, line=dict(color="#555", width=1))
        fig_trades.add_trace(go.Bar(
            x=trade_seq, y=closed["return_pct"],
            marker_color=colors, hovertext=hover, hoverinfo="text",
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
            f"Each bar is one trade. Green = win, red = loss. "
            f"Avg winner **{metrics['avg_winner_pct']:+.1f}%** · "
            f"avg loser **{metrics['avg_loser_pct']:.1f}%**."
            if metrics["avg_winner_pct"] and metrics["avg_loser_pct"]
            else "Each bar is one trade. Green = win, red = loss."
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
        st.dataframe(
            log[[
                "slot", "entry_ts", "exit_ts", "entry_price", "exit_price",
                "btc_bought", "start_value", "end_value", "gain_loss",
                "return_pct", "duration_hrs", "exit_reason",
            ]].rename(columns={
                "slot": "Slot", "entry_ts": "Entered", "exit_ts": "Exited",
                "entry_price": "BTC In", "exit_price": "BTC Out",
                "btc_bought": "BTC Bought", "start_value": "$ In", "end_value": "$ Out",
                "gain_loss": "P&L ($)", "return_pct": "Return %",
                "duration_hrs": "Hours Held", "exit_reason": "Exit Reason",
            }),
            use_container_width=True,
        )

    if show_save:
        st.divider()
        st.subheader("💾 Save This Run")
        _preset_save_ui(payload, result, display_name, params, metrics,
                        initial_capital, ending_capital, key_prefix)
