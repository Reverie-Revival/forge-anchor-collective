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
    # Momentum Rider — greens
    "Momentum Rider v1":   "#4ade80",
    "Momentum Rider v1r1": "#4ade80",
    "Momentum Rider v1r2": "#4ade80",
    "Momentum Rider v1r3": "#4ade80",
    "Momentum Rider v2":   "#22c55e",
    "Momentum Rider v3":   "#16a34a",
    "Momentum Rider v4":   "#15803d",
    # Dip Hunter — ambers/oranges
    "Dip Hunter v1":       "#f59e0b",
    "Dip Hunter v2":       "#f97316",
    "Dip Hunter v3":       "#ea580c",
    # Breakout Scout — blues/violets
    "Breakout Scout v1":   "#60a5fa",
    "Breakout Scout v2":   "#a78bfa",
    "Breakout Scout v3":   "#818cf8",
    # Volume Raider — pinks/roses
    "Volume Raider v1":    "#f472b6",
    "Volume Raider v2":    "#ec4899",
    # SMA Pullback — teals
    "SMA Pullback v1":     "#2dd4bf",
    # Cascade DCA — cyans/sky blues
    "Cascade DCA v1":      "#38bdf8",
    "Cascade DCA v2":      "#0ea5e9",
}
DEFAULT_COLORS = ["#94a3b8", "#fb923c", "#34d399", "#e879f9"]


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


# ── Overview dashboard ────────────────────────────────────────────────────────

def render_overview_dashboard(entries: list, key_prefix: str = "overview"):
    """
    Comparative view across multiple saved test windows.

    entries: list of dicts with keys:
        label, type, ann, dd, wr, trades, start, end, notes
    """
    import statistics

    if not entries:
        st.info("Select at least one window above.")
        return

    anns    = [e["ann"]    for e in entries if e["ann"]    is not None]
    dds     = [abs(e["dd"]) for e in entries if e["dd"]    is not None]
    trades  = [e["trades"] for e in entries if e["trades"] is not None]
    n       = len(entries)
    wins    = sum(1 for a in anns if a > 0)
    losses  = len(anns) - wins

    # ── Summary row ───────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Windows Tested", str(n))
    c2.metric("Positive / Negative", f"{wins} ✓  /  {losses} ✗")
    c3.metric("Avg Return",    f"{sum(anns)/len(anns):+.1f}%" if anns else "—")
    c4.metric("Median Return", f"{statistics.median(anns):+.1f}%" if anns else "—")
    c5.metric("Best Window",   f"{max(anns):+.1f}%" if anns else "—")
    c6.metric("Worst Window",  f"{min(anns):+.1f}%" if anns else "—")

    st.divider()

    # ── Bar chart — return per window ─────────────────────────────────────────
    bar_colors = [
        "#4ade80" if (e["ann"] or 0) > 10
        else "#facc15" if (e["ann"] or 0) > 0
        else "#f87171"
        for e in entries
    ]
    ann_vals = [round(e["ann"], 1) if e["ann"] is not None else 0 for e in entries]
    labels   = [e["label"] for e in entries]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels,
        y=ann_vals,
        marker_color=bar_colors,
        text=[f"{a:+.1f}%" for a in ann_vals],
        textposition="outside",
        cliponaxis=False,
    ))
    fig.add_hline(y=10,  line_dash="dash", line_color="#94a3b8",
                  annotation_text="S&P 500 (10%)", annotation_position="top right",
                  annotation_font_color="#94a3b8")
    fig.add_hline(y=0, line_color="#555555", line_width=1)
    fig.update_layout(
        yaxis_title="Annualized Return (%)",
        template="plotly_dark",
        height=420,
        margin=dict(t=20, b=80, l=60, r=20),
        showlegend=False,
        xaxis=dict(tickangle=-40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Max drawdown bar chart ─────────────────────────────────────────────────
    if any(e["dd"] is not None for e in entries):
        dd_vals = [abs(e["dd"]) if e["dd"] is not None else 0 for e in entries]
        fig_dd  = go.Figure()
        fig_dd.add_trace(go.Bar(
            x=labels,
            y=dd_vals,
            marker_color="#f97316",
            text=[f"{v:.1f}%" for v in dd_vals],
            textposition="outside",
            cliponaxis=False,
        ))
        fig_dd.update_layout(
            yaxis_title="Max Drawdown (%)",
            template="plotly_dark",
            height=280,
            margin=dict(t=10, b=80, l=60, r=20),
            showlegend=False,
            xaxis=dict(tickangle=-40),
        )
        st.plotly_chart(fig_dd, use_container_width=True)

    # ── Grade distribution ────────────────────────────────────────────────────
    st.divider()
    grade_counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    for e in entries:
        gnum, _, _ = grade_info(e["ann"])
        if gnum:
            grade_counts[gnum] = grade_counts.get(gnum, 0) + 1

    GRADE_META = {
        5: ("Elite  ≥20%",   "#00d4aa"),
        4: ("Strong 10-19%", "#4ade80"),
        3: ("Passing 8-9%",  "#facc15"),
        2: ("Weak  >0%",     "#fb923c"),
        1: ("Poor  ≤0%",     "#f87171"),
    }
    gcols = st.columns(5)
    for col, gnum in zip(gcols, [5, 4, 3, 2, 1]):
        label_g, color_g = GRADE_META[gnum]
        count = grade_counts.get(gnum, 0)
        col.markdown(
            f'<div style="text-align:center;padding:10px 4px;border-radius:8px;'
            f'background:{color_g}18;border:1px solid {color_g}44;">'
            f'<span style="color:{color_g};font-size:1.6rem;font-weight:700;">{count}</span><br>'
            f'<span style="color:{color_g};font-size:0.7rem;">{label_g}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Detail table ──────────────────────────────────────────────────────────
    st.divider()
    st.markdown('<p class="section-label">Window Detail</p>', unsafe_allow_html=True)

    table_rows = []
    for e in entries:
        ann = e["ann"]
        _, gl, gc = grade_info(ann)
        table_rows.append({
            "Window":   e["label"],
            "Period":   f"{str(e['start'])[:10]} → {str(e['end'])[:10]}",
            "Ann %":    f"{ann:+.1f}%" if ann is not None else "—",
            "Max DD":   f"{e['dd']:.1f}%" if e["dd"] is not None else "—",
            "Win Rate": f"{e['wr']*100:.0f}%" if e["wr"] is not None else "—",
            "Trades":   int(e["trades"]) if e["trades"] is not None else "—",
            "Grade":    gl.split(" · ")[-1] if " · " in gl else gl,
        })

    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
