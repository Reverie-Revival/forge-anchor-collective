import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import json
import streamlit as st
import pandas as pd

from src.app.utils import (
    grade_info, params_hash, label_window,
    _compact_config, _human_readable_description,
)
from src.app.db import (
    LAST_RUN_PATH, RUNS_DIR,
    load_stream_history, load_locked_streams,
    load_pending_runs, load_latest_run,
    next_run_number, load_run_payload, _pending_for_hash,
)
from src.app.dashboard import render_dashboard

KNOWN_STREAMS = [
    "Breakout Scout v1",
    "Dip Hunter v1",
    "Momentum Rider v1",
    "Momentum Rider v2",
    "Steady Climber v1",
    "Surge Rider v1",
]

st.title("⚓ Forge Anchor — Stream Tester")

# ── Glossary ──────────────────────────────────────────────────────────────────

GLOSSARY = {
    "EMA (Exponential Moving Average)":
        "A running average that weights recent prices more heavily. When a fast EMA "
        "crosses above a slow EMA, short-term momentum has flipped bullish.",
    "SMA (Simple Moving Average)":
        "A plain average of closing prices over N candles. Price above the 200 SMA "
        "means BTC is in a long-term uptrend.",
    "RSI (Relative Strength Index)":
        "0–100 score of momentum. Below 30 = oversold. Above 70 = overbought. "
        "45–65 is the active momentum zone.",
    "ATR (Average True Range)":
        "How much BTC moves per candle on average. High ATR = volatile. Low ATR = consolidating.",
    "Primary Timeframe":
        "Candle size for signal evaluation. 15m = most signals, most noise. "
        "1h = 4× less frequent, cleaner. Raw data is always 15m — resampling happens at run time.",
    "Trailing Stop":
        "Follows price upward; exits if price drops N% from its highest point since entry. "
        "Lets winning trades run while capping downside.",
    "Fear & Greed Index":
        "Daily 0–100 crypto sentiment score. 0 = Extreme Fear, 100 = Extreme Greed. "
        "Used as an entry gate — e.g. only enter when F&G < 20 to catch panic-driven dips.",
    "Profit Factor":
        "Total $ won ÷ total $ lost. Above 1.0 = profitable overall. 1.5 means $1.50 earned "
        "for every $1 lost, regardless of win rate.",
    "Slot":
        "Each stream can run in 1–2 slots with stream-specific behavior. "
        "'single' = one position at a time. 'scale_down' = slot 2 adds if price drops further (DH). "
        "'scale_up' = slot 2 adds to a winning position when trend confirms (MR).",
}

with st.expander("📖 Glossary"):
    cols = st.columns(2)
    for i, (term, defn) in enumerate(GLOSSARY.items()):
        cols[i % 2].markdown(f"**{term}**  \n{defn}")

with st.expander("🔧 Parameter Reference"):
    st.caption("All configurable attributes. Every filter is optional unless noted.")

    st.markdown("#### Core Signal *(required — pick one)*")
    signal_rows = [
        ("**ema_crossover**",  "EMA short crosses above EMA long",
         "`ema_short` (default 20), `ema_long` (default 50)"),
        ("**rsi_recovery**",   "RSI crosses back *up* through threshold — enters the bounce",
         "`rsi_threshold` (default 30), `rsi_period` (default 14), "
         "`require_bullish_candle` (bool, default false — also require close > prev close)"),
        ("**rsi_dip**",        "RSI below threshold AND price below SMA — enters continuous oversold",
         "`rsi_threshold` (default 35), `sma_period` (default 20), `dip_pct` (default 2.0%)"),
        ("**fear_dip**",       "Price drops N% below SMA or previous candle — no RSI required",
         "`dip_pct` (default 3.0%), `sma_period` (optional; if omitted, compares to prev candle)"),
        ("**sma_pullback**",   "Price pulls back near SMA during uptrend then bounces",
         "`pullback_sma` (default 50), `trend_sma` (default 200), "
         "`pullback_tolerance_pct` (default 1.5%)"),
        ("**range_breakout**", "Price breaks above highest point over lookback window",
         "`breakout_lookback` (default 48 candles)"),
        ("**volume_surge**",   "Bullish candle on volume spike above multiplier × average",
         "`volume_multiplier` (default 2.5), `volume_avg_period` (default 20)"),
    ]
    c1, c2, c3 = st.columns([1.4, 2, 3])
    c1.markdown("**Signal**"); c2.markdown("**What it does**"); c3.markdown("**Parameters**")
    st.divider()
    for name, desc, params_str in signal_rows:
        c1, c2, c3 = st.columns([1.4, 2, 3])
        c1.markdown(name); c2.markdown(desc); c3.markdown(params_str)

    st.markdown("---")
    st.markdown("#### Filters *(all optional)*")
    filter_rows = [
        ("**drawdown_from_high**", "Require price to have dropped ≥ N% from its recent high",
         "`lookback_days` (how far back to find the high, e.g. 90), "
         "`min_drop_pct` (required % drop, e.g. 25)"),
        ("**trend_context**",      "Require price above or below long-term SMA",
         "`sma_period` (e.g. 200), `require`: `\"above\"` or `\"below\"`"),
        ("**sentiment.fear_greed**", "Gate on daily Fear & Greed Index (0–100)",
         "`min` (e.g. 25 = only trade above fear), `max` (e.g. 20 = only in extreme fear)"),
        ("**rsi**",                "RSI range filter at the moment of entry",
         "`min` (e.g. 55 for momentum), `max` (e.g. 40 for oversold), `period` (default 14)"),
        ("**volume**",             "Require above-average volume at entry",
         "`min_multiplier` (e.g. 1.5 = 1.5× average), `avg_period` (default 20)"),
        ("**atr_regime**",         "Only enter when volatility is low (post-panic calm)",
         "`period` (default 14), `avg_period` (default 30), "
         "`max_pct_of_avg` (e.g. 70 = ATR must be < 70% of its average), "
         "`min_consecutive_candles` (optional — ATR must be low for N consecutive candles, "
         "confirms genuine consolidation not a single quiet candle)"),
        ("**bollinger**",          "Bollinger Band squeeze — confirms price compression independently of ATR",
         "`period` (default 20), `std_dev` (default 2.0), "
         "`squeeze.max_bandwidth_pct` (e.g. 6.0 = bands must be < 6% of price — "
         "low bandwidth = tight consolidation)"),
        ("**breakout_candle**",    "Quality check on the entry candle — filters wick fakeouts",
         "`body_ratio_min` (e.g. 0.4 = body must be ≥ 40% of candle range), "
         "`close_position_min` (e.g. 0.6 = close must be in top 40% of candle range — conviction)"),
    ]
    c1, c2, c3 = st.columns([1.6, 2, 3])
    c1.markdown("**Filter**"); c2.markdown("**What it does**"); c3.markdown("**Parameters**")
    st.divider()
    for name, desc, params_str in filter_rows:
        c1, c2, c3 = st.columns([1.6, 2, 3])
        c1.markdown(name); c2.markdown(desc); c3.markdown(params_str)

    st.markdown("---")
    st.markdown("#### Position / Exit")
    position_rows = [
        ("**trailing_stop_pct**",    "% drop from peak since entry to trigger exit", "e.g. 7.5"),
        ("**min_hold_candles**",     "Stop cannot fire before this many candles — lets the trade develop",
         "e.g. 48 (= 48 hours on 1h timeframe)"),
        ("**max_hold_candles**",     "Force-exit after N candles regardless of P&L", "optional"),
        ("**entry_order_type**",     "Always `limit` (Kraken maker fee = 0.25% vs 0.40% taker)", "limit"),
        ("**entry_expiry_candles**", "Cancel pending limit order if not filled within N candles",
         "default 2"),
        ("**partial_exit**",        "Take a portion off at a target gain, trail the remainder — "
         "locks profit while keeping upside exposure",
         "`at_gain_pct` (e.g. 5.0 = take partial at +5%), `exit_pct` (e.g. 50 = close half)"),
    ]
    c1, c2, c3 = st.columns([1.8, 2.8, 1.4])
    c1.markdown("**Parameter**"); c2.markdown("**What it does**"); c3.markdown("**Default / Example**")
    st.divider()
    for name, desc, default in position_rows:
        c1, c2, c3 = st.columns([1.8, 2.8, 1.4])
        c1.markdown(name); c2.markdown(desc); c3.markdown(default)

    st.markdown("---")
    st.markdown("#### Timeframe")
    st.markdown(
        "**primary_timeframe** — candle size for signal evaluation. "
        "Options: `15m` · `1h` · `4h` · `1d`. "
        "Raw data is always 15m candles; resampling happens at run time. "
        "Coarser timeframes fire less often but with much less noise."
    )

# ── Load data ─────────────────────────────────────────────────────────────────

history        = load_stream_history()
locked_streams = load_locked_streams()

run_groups = {}
if not history.empty:
    for _, row in history.iterrows():
        skey = f"{row['stream_name']} {row['stream_version']}"
        rnum = int(row["run_number"]) if pd.notna(row.get("run_number")) else 0
        run_groups.setdefault(skey, {}).setdefault(rnum, []).append(row)

all_streams = list(dict.fromkeys(
    KNOWN_STREAMS + sorted(s for s in run_groups if s not in KNOWN_STREAMS)
))

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Stream Tester")
    st.caption("Triggered from Claude Code · Save to record here")

    st.markdown('<p class="config-group-header">Stream</p>', unsafe_allow_html=True)
    if not all_streams:
        st.info("No streams yet.")
        st.stop()

    selected_stream = st.selectbox("", all_streams, label_visibility="collapsed")
    stream_runs     = run_groups.get(selected_stream, {})

    st.markdown('<p class="config-group-header" style="margin-top:14px;">Test Run</p>',
                unsafe_allow_html=True)

    run_options = []
    run_labels  = {}

    for rnum in sorted(stream_runs.keys()):
        rows = stream_runs[rnum]
        best = max(
            (r["annualized_return_pct"] for r in rows if pd.notna(r["annualized_return_pct"])),
            default=None,
        )
        lbl = f"#{rnum}"
        if best is not None:
            lbl += f"  ·  {best:+.1f}%"
        if len(rows) > 1:
            lbl += f"  ·  {len(rows)} windows"
        run_options.append(rnum)
        run_labels[rnum] = lbl

    latest_run_preview = load_latest_run(selected_stream)
    latest_is_new      = False
    latest_matches_run = None

    if latest_run_preview:
        lr_ph = params_hash(latest_run_preview["params"])

        for rnum, rrows in stream_runs.items():
            for row in rrows:
                try:
                    p = row["parameters"] if isinstance(row["parameters"], dict) \
                        else json.loads(row["parameters"])
                    if params_hash(p) == lr_ph:
                        latest_matches_run = rnum
                        break
                except Exception:
                    pass
            if latest_matches_run is not None:
                break

        if latest_matches_run is None:
            latest_is_new  = True
            stream_nm_only = selected_stream.rsplit(" ", 1)[0]
            new_run_num    = next_run_number(stream_nm_only, lr_ph, history)
            run_options.append("__new__")
            run_labels["__new__"] = f"⏳ Run #{new_run_num} — unsaved"

    if not run_options or run_options == ["__new__"]:
        if not latest_is_new:
            st.caption("No saved runs for this stream yet.")
        selected_run = "__new__" if latest_is_new else None
    else:
        if latest_matches_run is not None and latest_matches_run in run_options:
            default_idx = run_options.index(latest_matches_run)
        else:
            default_idx = len(run_options) - 1
        selected_run = st.selectbox(
            "", run_options,
            index=default_idx,
            format_func=lambda x: run_labels.get(x, str(x)),
            label_visibility="collapsed",
        )

    # Config details panel
    if selected_run and selected_run != "__new__" and selected_run in stream_runs:
        st.divider()
        rows = stream_runs[selected_run]
        try:
            p       = rows[0]["parameters"] if isinstance(rows[0]["parameters"], dict) \
                      else json.loads(rows[0]["parameters"])
            compact = _compact_config(p)
        except Exception:
            p, compact = {}, "—"

        stream_key  = f"{rows[0]['stream_name']} {rows[0]['stream_version']}"
        locked_info = locked_streams.get(stream_key, {})
        description = locked_info.get("description")
        locked_grade = locked_info.get("grade")

        st.markdown(f"**{rows[0]['stream_name']} {rows[0]['stream_version']}**")

        if locked_grade is not None:
            grade_labels = {5: "Grade 5 · Elite", 4: "Grade 4 · Strong",
                            3: "Grade 3 · Passing", 2: "Grade 2 · Weak", 1: "Grade 1 · Poor"}
            grade_colors = {5: "#00d4aa", 4: "#4ade80", 3: "#facc15",
                            2: "#fb923c",  1: "#f87171"}
            gl = grade_labels.get(locked_grade, "—")
            gc = grade_colors.get(locked_grade, "#555")
            st.markdown(
                f'<span class="grade-badge" style="background:{gc}22;color:{gc};'
                f'border:1px solid {gc}66;font-size:0.75rem;padding:3px 10px;">'
                f'🔒 Locked · {gl}</span>',
                unsafe_allow_html=True,
            )

        st.markdown(description if description else f"*{_human_readable_description(p)}*")

        with st.expander("Signal details"):
            st.caption(compact)

        st.divider()

        for row in rows:
            win = row.get("timeframe_label") or label_window(
                row.get("simulation_start") or row.get("custom_start"),
                row.get("simulation_end") or row.get("custom_end"),
            )
            ann = row["annualized_return_pct"]
            pf  = row["profit_factor"]
            _, gl, gc = grade_info(ann if pd.notna(ann) else None)
            st.markdown(
                f'<span class="grade-badge" style="background:{gc}20;color:{gc};'
                f'border:1px solid {gc}55;font-size:0.75rem;padding:3px 10px;">'
                f'{win}  ·  {ann:+.1f}%</span>' if pd.notna(ann) else
                f'<span class="grade-badge" style="background:#33333380;color:#aaa;'
                f'border:1px solid #55555555;font-size:0.75rem;padding:3px 10px;">'
                f'{win}</span>',
                unsafe_allow_html=True,
            )
            if pd.notna(pf):
                st.caption(
                    f"PF {pf:.2f}  ·  DD {row['max_drawdown_pct']:.1f}%  ·  "
                    f"WR {row['win_rate']*100:.0f}%  ·  {row['total_trades']} trades"
                )
            if row.get("notes"):
                st.caption(f"💬 {row['notes']}")


# ── Main area ─────────────────────────────────────────────────────────────────

def _render_pending_tabs(pending: list):
    """Render a list of pending runs as labelled tabs with save buttons."""
    tab_labels = [f"⏳ {label_window(pr['start'], pr['end'])}" for pr in pending]
    tabs = st.tabs(tab_labels)
    for tab, pr in zip(tabs, pending):
        with tab:
            render_dashboard(pr["payload"], show_save=True,
                             key_prefix=f"new_{pr['start']}_{pr['end']}")


if selected_run == "__new__" and latest_run_preview:
    lr_ph   = params_hash(latest_run_preview["params"])
    pending = _pending_for_hash(lr_ph)
    if pending:
        _render_pending_tabs(pending)
    else:
        render_dashboard(latest_run_preview, show_save=True, key_prefix="new_run")

elif selected_run is not None and selected_run in stream_runs:
    saved_rows   = stream_runs[selected_run]
    pending_runs = load_pending_runs(saved_rows)

    saved_entries = []
    for row in saved_rows:
        lbl = row.get("timeframe_label") or label_window(
            row.get("simulation_start") or row.get("custom_start"),
            row.get("simulation_end") or row.get("custom_end"),
        )
        saved_entries.append({"label": lbl, "type": "saved", "data": row})
    saved_entries.sort(key=lambda e: e["label"])

    tab_entries = saved_entries
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
                    # No pkl — show DB summary only
                    ann = row["annualized_return_pct"]
                    pf  = row["profit_factor"]
                    dd  = row["max_drawdown_pct"]
                    wr  = row["win_rate"]
                    _, gl, gc = grade_info(ann if pd.notna(ann) else None)
                    st.markdown(
                        f'<span class="grade-badge" style="background:{gc}22;color:{gc};'
                        f'border:1px solid {gc}66;font-size:1rem;">{gl}</span>',
                        unsafe_allow_html=True,
                    )
                    sim_s = str(row.get("simulation_start") or "")[:10]
                    sim_e = str(row.get("simulation_end") or "")[:10]
                    st.caption(
                        f"{row['stream_name']} {row['stream_version']}  ·  {sim_s} → {sim_e}"
                    )
                    st.divider()
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Annualized Return", f"{ann:+.1f}%" if pd.notna(ann) else "—")
                    c2.metric("Profit Factor",     f"{pf:.2f}"    if pd.notna(pf)  else "—")
                    c3.metric("Max Drawdown",      f"{dd:.1f}%"   if pd.notna(dd)  else "—")
                    c4.metric("Win Rate",          f"{wr*100:.0f}%" if pd.notna(wr) else "—")
                    st.info("Re-run from Claude Code to restore full charts.")
                    if row.get("notes"):
                        st.caption(f"💬 {row['notes']}")
            else:
                pr = entry["data"]
                render_dashboard(pr["payload"], show_save=True,
                                 key_prefix=f"pending_{pr['start']}_{pr['end']}")

else:
    latest_run = load_latest_run(selected_stream)
    if latest_run:
        tabs = st.tabs(["⏳ Latest Run"])
        with tabs[0]:
            render_dashboard(latest_run, show_save=True, key_prefix="latest_run_new")
    else:
        st.info("No runs yet for this stream. Trigger one from Claude Code.")
