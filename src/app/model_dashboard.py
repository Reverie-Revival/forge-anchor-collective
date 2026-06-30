"""
Dashboard renderer for a model-level backtest run.
Shows combined model metrics, per-stream racing lines, and individual stream breakdowns.
"""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from .utils import SP500_HISTORICAL_AVG, fetch_sp500, grade_info
from .db import load_model_history, load_timeframe_presets, save_model_test

# Stream colors — consistent across all charts
STREAM_COLORS = {
    "Momentum Rider v1": "#4ade80",   # green  — bull trending
    "Momentum Rider v2": "#22c55e",   # green (deeper) — MR upgraded
    "Dip Hunter v1":     "#f59e0b",   # amber  — bear recovery
    "Breakout Scout v1": "#60a5fa",   # blue   — breakout
}
DEFAULT_COLORS = ["#a78bfa", "#f472b6", "#34d399"]


def _stream_color(name: str, idx: int = 0) -> str:
    return STREAM_COLORS.get(name, DEFAULT_COLORS[idx % len(DEFAULT_COLORS)])


def _render_model_save(payload, key_prefix):
    st.divider()
    st.subheader("💾 Save This Run")

    presets   = load_timeframe_presets()
    run_start = pd.Timestamp(payload["start"]).date()
    run_end   = pd.Timestamp(payload["end"]).date()

    def _matches(preset) -> bool:
        ps = pd.Timestamp(preset["start_date"]).date()
        pe = preset["end_date"]
        if abs((run_start - ps).days) > 5:
            return False
        if pe is None:
            return True
        pe = pd.Timestamp(pe).date()
        return abs((run_end - pe).days) <= 5

    matched_preset_id = None
    for p in presets:
        if _matches(p):
            matched_preset_id = p["preset_id"]
            break

    options     = [p["name"] for p in presets] + ["Custom"]
    default_idx = next((i for i, p in enumerate(presets) if p["preset_id"] == matched_preset_id),
                       len(options) - 1)

    sc1, sc2 = st.columns([1, 2])
    selected_label = sc1.selectbox("Timeframe", options, index=default_idx,
                                   key=f"{key_prefix}_preset_sel")
    save_notes     = sc2.text_input("Notes (optional)", key=f"{key_prefix}_notes",
                                    placeholder="e.g. equal allocation baseline")

    save_preset_id    = None
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
            history_df = load_model_history()
            model_test_id, run_num = save_model_test(
                payload=payload,
                preset_id=save_preset_id,
                custom_start=save_custom_start,
                custom_end=save_custom_end,
                notes=save_notes, history=history_df,
            )
            cm  = payload["combined_metrics"]
            ann = cm["annualized_return_pct"]
            ann_str = f"{ann:+.1f}% annualized" if ann is not None else "no trades"
            timeframe_label = selected_label if selected_label != "Custom" else (
                f"{save_custom_start} → {save_custom_end or 'present'}"
            )
            st.success(
                f"Saved — Run #{run_num} · {timeframe_label} · "
                f"{cm['total_trades']} trades · {ann_str}."
            )
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")


def render_model_dashboard(payload: dict, show_save: bool = True, key_prefix: str = "mdash"):
    stream_results  = payload["stream_results"]
    combined_trades = payload["combined_trades"]
    cm              = payload["combined_metrics"]
    total_capital   = payload["total_capital"]
    bh              = payload.get("bh", {})
    period_start    = payload["start"]
    period_end      = payload["end"]
    period_str      = f"{period_start.date()} → {period_end.date()}"

    total_ending  = total_capital + (cm["total_pnl"] or 0)
    ann           = cm["annualized_return_pct"]
    _, grade_label, grade_color = grade_info(ann)
    has_trades    = not combined_trades.empty and cm["total_trades"] > 0

    n_streams = len(stream_results)
    alloc_summary = "  ·  ".join(
        f"{sr['stream_name'].split(' ')[0]} \\${sr['lot_size_usd']:.2f} × {sr['slot_count']} slots"
        for sr in stream_results
    )

    # Header
    col_title, col_grade = st.columns([3, 1])
    with col_title:
        st.subheader(f"Model 1 — {n_streams} Streams")
        st.caption(f"{period_str}  ·  \\${total_capital:.2f} capital  ·  {alloc_summary}  ·  {cm['total_trades']} trades")
    with col_grade:
        st.markdown(
            f'<div style="text-align:right;margin-top:8px;">'
            f'<span class="grade-badge" style="background:{grade_color}22;'
            f'color:{grade_color};border:1px solid {grade_color}66;font-size:1rem;">'
            f'{grade_label}</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # Combined KPIs
    st.markdown('<p class="section-label">Combined Performance</p>', unsafe_allow_html=True)
    sp500_actual = fetch_sp500(str(period_start.date()), str(period_end.date()))
    sp500_ann    = sp500_actual["annualized_return_pct"] if sp500_actual else None
    bh_ann       = bh.get("annualized_return_pct")

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Total Capital", f"${total_capital:.2f}",
              help=f"{n_streams} streams · ${total_capital/n_streams:.0f} avg each")
    h2.metric("Ending Balance", f"${total_ending:.2f}",
              delta=f"{total_ending - total_capital:+.2f}", delta_color="normal")
    h3.metric(
        "Annualized Return",
        f"{ann:+.1f}%" if ann is not None else "—",
        delta=f"{ann - SP500_HISTORICAL_AVG:+.1f}% vs S&P avg" if ann is not None else None,
        delta_color="normal",
    )
    h4.metric("Total Return",
              f"{cm['total_return_pct']:+.1f}%",
              delta=f"{ann:+.1f}% / year" if ann is not None else None,
              delta_color="normal")

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

    # Per-stream allocation + results table
    st.markdown('<p class="section-label">Stream Breakdown</p>', unsafe_allow_html=True)
    cols = st.columns(n_streams)
    for col, sr in zip(cols, stream_results):
        color   = _stream_color(sr["stream_name"])
        sm      = sr["metrics"]
        s_ann   = sm["annualized_return_pct"]
        gain    = sr["ending_balance"] - sr["initial_capital"]
        _, s_gl, s_gc = grade_info(s_ann)
        s_trades = sr["trades"]
        if not s_trades.empty:
            s_closed  = s_trades[s_trades["exit_reason"] != "partial"]
            s_wins    = int((s_closed["pnl"] > 0).sum())
            s_losses  = int((s_closed["pnl"] <= 0).sum())
        else:
            s_wins = s_losses = 0
        with col:
            st.markdown(
                f'<span style="color:{color};font-weight:700;">{sr["stream_name"]}</span>',
                unsafe_allow_html=True,
            )
            st.caption(
                f"\\${sr['lot_size_usd']:.2f}/lot × {sr['slot_count']} slots = "
                f"\\${sr['initial_capital']:.2f}"
            )
            st.metric(
                "Ending Balance",
                f"${sr['ending_balance']:.2f}",
                delta=f"{gain:+.2f}  ·  {s_ann:+.1f}%/yr" if s_ann is not None else f"{gain:+.2f}",
                delta_color="normal",
            )
            if sm["win_rate"]:
                st.caption(
                    f"Trades: {sm['total_trades']}  ·  "
                    f"WR: {sm['win_rate']*100:.0f}%  ·  "
                    f"{s_wins}W / {s_losses}L"
                )
            else:
                st.caption(f"Trades: {sm['total_trades']}")
            if sm.get("profit_factor"):
                st.caption(f"PF: {sm['profit_factor']}  ·  DD: {sm['max_drawdown_pct']:.1f}%")

    st.divider()

    if not has_trades:
        st.warning("No trades were generated across any stream with this allocation.")
        if show_save:
            _render_model_save(payload, key_prefix)
        return

    # Racing lines — per-stream balance over time
    st.markdown('<p class="section-label">Stream Performance Over Time</p>',
                unsafe_allow_html=True)
    fig_race = go.Figure()

    for idx, sr in enumerate(stream_results):
        color  = _stream_color(sr["stream_name"], idx)
        trades = sr["trades"]
        init   = sr["initial_capital"]

        if not trades.empty:
            closed  = trades[trades["exit_reason"] != "partial"].sort_values("exit_ts")
            balance = (init + closed["pnl"].cumsum()).tolist()
            xs = [period_start] + list(closed["exit_ts"]) + [period_end]
            ys = [init] + balance + [balance[-1]]
        else:
            xs = [period_start, period_end]
            ys = [init, init]

        fig_race.add_trace(go.Scatter(
            x=xs, y=ys,
            name=sr["stream_name"],
            line=dict(color=color, width=2.5),
            hovertemplate=(
                f"<b>{sr['stream_name']}</b><br>"
                "%{x|%b %d, %Y}<br>Balance: $%{y:.2f}<extra></extra>"
            ),
        ))

    fig_race.update_layout(
        template="plotly_dark", title="Stream Balance Over Time",
        xaxis_title=None, yaxis_title="Balance ($)",
        height=380, margin=dict(t=40, b=20, l=10, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_race, use_container_width=True, key=f"{key_prefix}_race")

    # Combined equity curve
    st.markdown('<p class="section-label">Combined Account Balance</p>', unsafe_allow_html=True)
    st.caption("Click a stream name in the legend to overlay its solo $100 performance.")
    if has_trades:
        all_closed  = combined_trades[combined_trades["exit_reason"] != "partial"].sort_values("exit_ts")
        equity      = total_capital + all_closed["pnl"].cumsum()
        peak_val    = equity.max()
        peak_ts     = all_closed.loc[equity.idxmax(), "exit_ts"]
        low_val     = equity.min()
        low_ts      = all_closed.loc[equity.idxmin(), "exit_ts"]
        profitable  = total_ending >= total_capital
        curve_color = "#00d4aa" if profitable else "#f87171"
        fill_color  = "rgba(0,212,170,0.07)" if profitable else "rgba(248,113,113,0.07)"

        fig_eq = go.Figure()

        # Combined balance — always visible, filled
        fig_eq.add_trace(go.Scatter(
            x=all_closed["exit_ts"], y=equity,
            name="Combined (all streams)",
            fill="tozeroy", fillcolor=fill_color,
            line=dict(color=curve_color, width=2.5),
            hovertemplate="<b>Combined</b><br>%{x|%b %d, %Y}<br>Balance: $%{y:.2f}<extra></extra>",
        ))

        # Per-stream solo overlays — hidden by default, toggle via legend
        for idx, sr in enumerate(stream_results):
            s_trades = sr["trades"]
            if s_trades.empty:
                continue
            scale        = total_capital / sr["initial_capital"]
            s_closed     = s_trades[s_trades["exit_reason"] != "partial"].sort_values("exit_ts")
            solo_balance = total_capital + (s_closed["pnl"] * scale).cumsum()
            color        = _stream_color(sr["stream_name"], idx)
            fig_eq.add_trace(go.Scatter(
                x=s_closed["exit_ts"], y=solo_balance,
                name=f"{sr['stream_name']} solo",
                visible="legendonly",
                line=dict(color=color, width=1.8, dash="dot"),
                hovertemplate=(
                    f"<b>{sr['stream_name']} solo</b><br>"
                    "%{x|%b %d, %Y}<br>Balance: $%{y:.2f}<extra></extra>"
                ),
            ))

        fig_eq.add_hline(y=total_capital, line=dict(color="#555", dash="dot"),
                         annotation_text=f"Start \${total_capital:.2f}",
                         annotation_font_color="#888")
        fig_eq.add_trace(go.Scatter(
            x=[peak_ts], y=[peak_val], mode="markers+text",
            marker=dict(color="#4ade80", size=10),
            text=[f"  <b>High ${peak_val:.2f}</b>"],
            textposition="middle right", textfont=dict(color="#4ade80", size=12),
            showlegend=False,
            hovertemplate=f"Peak — {peak_ts.strftime('%b %d, %Y')} — ${peak_val:.2f}<extra></extra>",
        ))
        fig_eq.add_trace(go.Scatter(
            x=[low_ts], y=[low_val], mode="markers+text",
            marker=dict(color="#f87171", size=10),
            text=[f"  <b>Low ${low_val:.2f}</b>"],
            textposition="middle right", textfont=dict(color="#f87171", size=12),
            showlegend=False,
            hovertemplate=f"Trough — {low_ts.strftime('%b %d, %Y')} — ${low_val:.2f}<extra></extra>",
        ))
        fig_eq.update_layout(
            template="plotly_dark",
            title=None,
            xaxis_title=None, yaxis_title="Balance ($)",
            height=380, margin=dict(t=20, b=20, l=10, r=10),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                bgcolor="rgba(0,0,0,0)", borderwidth=0,
            ),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_eq, use_container_width=True, key=f"{key_prefix}_eq")

    # Combined trade stats + drawdown
    col_stats, col_dd = st.columns([1, 1])

    with col_stats:
        st.markdown('<p class="section-label">Combined Trade Statistics</p>',
                    unsafe_allow_html=True)
        s1, s2 = st.columns(2)
        s3, s4 = st.columns(2)
        n_wins   = len(all_closed[all_closed["pnl"] > 0]) if has_trades else 0
        n_losses = len(all_closed[all_closed["pnl"] <= 0]) if has_trades else 0
        s1.metric("Win Rate",
                  f"{cm['win_rate']*100:.1f}%" if cm["win_rate"] else "—",
                  delta=f"{n_wins}W · {n_losses}L", delta_color="off")
        s2.metric("Profit Factor", str(cm["profit_factor"]) if cm["profit_factor"] else "—")
        s3.metric("Max Drawdown",
                  f"{cm['max_drawdown_pct']:.1f}%" if cm["max_drawdown_pct"] is not None else "—",
                  delta="combined peak-to-trough", delta_color="off")
        s4.metric("Avg Winner",
                  f"{cm['avg_winner_pct']:+.1f}%" if cm["avg_winner_pct"] else "—")

    with col_dd:
        if has_trades:
            peak_eq  = equity.cummax()
            drawdown = (equity - peak_eq) / peak_eq * 100
            worst    = drawdown.min()
            fig_dd   = go.Figure(go.Scatter(
                x=all_closed["exit_ts"], y=drawdown,
                fill="tozeroy", line=dict(color="#f87171", width=1.5),
                fillcolor="rgba(248,113,113,0.12)",
                hovertemplate="<b>%{x|%b %d, %Y}</b><br>%{y:.1f}% below peak<extra></extra>",
            ))
            fig_dd.update_layout(
                template="plotly_dark", title=f"Drawdown  (worst: {worst:.1f}%)",
                xaxis_title=None, yaxis_title="% below peak",
                height=260, margin=dict(t=40, b=20, l=10, r=10),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_dd, use_container_width=True, key=f"{key_prefix}_dd")

    # Trade log
    with st.expander(f"Combined Trade Log ({cm['total_trades']} trades)", expanded=False):
        log = all_closed.copy()
        log["return_pct"] = (
            (log["exit_price"] - log["entry_price"]) / log["entry_price"] * 100
        ).round(2)
        log["gain_loss"]   = log["pnl"].round(4)
        log["entry_price"] = log["entry_price"].round(2)
        log["exit_price"]  = log["exit_price"].round(2)
        st.dataframe(
            log[[
                "stream_name", "slot", "entry_ts", "exit_ts",
                "entry_price", "exit_price", "capital",
                "gain_loss", "return_pct", "exit_reason",
            ]].rename(columns={
                "stream_name": "Stream", "slot": "Slot",
                "entry_ts": "Entered", "exit_ts": "Exited",
                "entry_price": "BTC In", "exit_price": "BTC Out",
                "capital": "$ In", "gain_loss": "P&L ($)",
                "return_pct": "Return %", "exit_reason": "Exit Reason",
            }),
            use_container_width=True,
        )

    if show_save:
        _render_model_save(payload, key_prefix)
