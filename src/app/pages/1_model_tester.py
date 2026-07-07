import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

import json
import re
import streamlit as st
import pandas as pd

from src.app.utils import grade_info, label_window, _compact_config
from src.app.db import (
    MODEL_LAST_RUN_PATH, MODEL_RUNS_DIR,
    load_models, load_model_history, load_locked_streams_full,
    load_pending_model_runs, load_last_model_run,
    next_model_run_number, load_model_run_payload,
    _pending_model_for_hash, _alloc_hash,
)
from src.app.model_dashboard import render_model_dashboard, render_overview_dashboard, STREAM_COLORS

st.set_page_config(layout="wide")
st.title("⚓ Forge Anchor — Model Tester")

# ── Glossary ──────────────────────────────────────────────────────────────────

GLOSSARY = {
    "Model":
        "A complete trading configuration — multiple streams running simultaneously with "
        "separate capital allocations. Model 1 has 3 streams and $60 total capital.",
    "Stream":
        "One strategy within a model. Each stream has its own entry signal, filters, "
        "and capital allocation. They fire independently.",
    "Lot Size (lot_size_usd)":
        "Dollars committed per trade slot within a stream. Min $10 (Kraken minimum + fee floor).",
    "Slot Count":
        "Max concurrent positions a stream can hold. 2 slots = the stream can have 2 open "
        "trades at once if signals fire close together.",
    "Total Capital":
        "Σ(lot_size × slot_count) across all streams. Each stream manages its own capital "
        "pool — they don't compete for the same dollars.",
    "Racing Lines":
        "Per-stream % return over time plotted on one chart. Reveals which streams are "
        "contributing in which market regimes.",
    "Trailing Stop":
        "Follows price upward; exits if price drops N% from its highest point since entry.",
    "Profit Factor":
        "Total $ won ÷ total $ lost. Above 1.0 = profitable. Measured across all streams combined.",
}

with st.expander("📖 Glossary"):
    cols = st.columns(2)
    for i, (term, defn) in enumerate(GLOSSARY.items()):
        cols[i % 2].markdown(f"**{term}**  \n{defn}")

# ── Stream Reference ───────────────────────────────────────────────────────────

locked_streams_full = load_locked_streams_full()

with st.expander("🔒 Stream Reference — locked configs used in this model"):
    if not locked_streams_full:
        st.caption("No locked streams found in backtest.streams.")
    else:
        ref_cols = st.columns(len(locked_streams_full))
        for col, sc in zip(ref_cols, locked_streams_full):
            color = STREAM_COLORS.get(sc["full_name"], "#aaa")
            with col:
                st.markdown(
                    f'<span style="color:{color};font-weight:700;">{sc["full_name"]}</span>',
                    unsafe_allow_html=True,
                )
                if sc.get("description"):
                    st.caption(sc["description"])
                st.caption(
                    f"Default: \\${sc['lot_size_usd']:.2f}/lot × {sc['slot_count']} slots "
                    f"= \\${sc['lot_size_usd'] * sc['slot_count']:.2f}"
                )
                p = sc["params"]
                if p.get("core_signal"):
                    st.caption(f"Signal: `{p['core_signal']}`")
                sentiment = p.get("sentiment", {}).get("fear_greed", {})
                if sentiment.get("min"):
                    st.caption(f"F&G ≥ {sentiment['min']}")
                if sentiment.get("max"):
                    st.caption(f"F&G ≤ {sentiment['max']}")
                pos = p.get("position", {})
                if pos.get("trailing_stop_pct"):
                    st.caption(f"Trail stop: {pos['trailing_stop_pct']}%")
                with st.expander("Full config"):
                    st.caption(_compact_config(p))

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Model Tester")

    all_models = load_models()
    model_options = {m["model_id"]: f"Model {m['model_version']}" for m in all_models}
    selected_model_id = st.selectbox(
        "Model",
        options=list(model_options.keys()),
        format_func=lambda mid: model_options[mid],
        index=len(model_options) - 1,
    )
    selected_model_version = model_options.get(selected_model_id, "Model ?")

    st.markdown('<p class="config-group-header" style="margin-top:4px;">Test Run</p>',
                unsafe_allow_html=True)

    history = load_model_history(model_id=selected_model_id)
    run_groups = {}
    if not history.empty:
        for _, row in history.iterrows():
            rnum = int(row["run_number"]) if pd.notna(row.get("run_number")) else 0
            run_groups.setdefault(rnum, []).append(row)

    run_options = []
    run_labels  = {}

    def _abbrev(name):
        parts = name.split()
        return f"{''.join(w[0] for w in parts[:-1])} {parts[-1]}"

    for rnum in sorted(run_groups.keys()):
        rows = run_groups[rnum]
        best = max(
            (r["annualized_return_pct"] for r in rows if pd.notna(r["annualized_return_pct"])),
            default=None,
        )
        try:
            cfg = rows[0]["configuration"]
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            alloc_keys = list(cfg.get("allocations", {}).keys())
            stream_tag = " · ".join(_abbrev(k) for k in alloc_keys)
        except Exception:
            stream_tag = ""
        lbl = f"#{rnum}"
        if stream_tag:
            lbl += f"  {stream_tag}"
        if best is not None:
            lbl += f"  ·  {best:+.1f}%"
        if len(rows) > 1:
            lbl += f"  ·  {len(rows)} windows"
        run_options.append(rnum)
        run_labels[rnum] = lbl

    latest_run = load_last_model_run()
    latest_is_new      = False
    latest_matches_run = None

    if latest_run:
        alloc     = latest_run.get("allocations", {})
        lr_ah     = _alloc_hash(alloc)

        for rnum, rrows in run_groups.items():
            for row in rrows:
                try:
                    cfg = row["configuration"]
                    if isinstance(cfg, str):
                        cfg = json.loads(cfg)
                    if _alloc_hash(cfg.get("allocations", {})) == lr_ah:
                        latest_matches_run = rnum
                        break
                except Exception:
                    pass
            if latest_matches_run is not None:
                break

        if latest_matches_run is None:
            latest_is_new  = True
            new_run_num    = next_model_run_number(
                latest_run.get("model_id", 1), lr_ah
            )
            run_options.append("__new__")
            run_labels["__new__"] = f"⏳ Run #{new_run_num} — unsaved"

    if not run_options or run_options == ["__new__"]:
        if not latest_is_new:
            st.caption("No saved runs yet. Trigger one from Claude Code.")
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

    # Allocation + window summary for selected run
    if selected_run and selected_run != "__new__" and selected_run in run_groups:
        st.divider()
        rows = run_groups[selected_run]
        try:
            cfg = rows[0]["configuration"]
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            alloc = cfg.get("allocations", {})
        except Exception:
            alloc = {}

        total_cap = rows[0].get("total_capital")
        st.markdown(f"**{selected_model_version}  ·  {len(alloc)} streams**")
        if total_cap and pd.notna(total_cap):
            st.caption(f"Total capital: ${total_cap:.0f}")

        for stream_name, a in alloc.items():
            color    = STREAM_COLORS.get(stream_name, "#aaa")
            lot      = a.get("lot_size_usd", 10)
            slots    = a.get("slot_count", 2)
            subtotal = lot * slots
            st.markdown(
                f'<span style="color:{color};font-size:0.8rem;">{stream_name}</span>  '
                f'<span style="color:#888;font-size:0.8rem;">'
                f'\\${lot:.2f}/lot × {slots} = <b>\\${subtotal:.2f}</b></span>',
                unsafe_allow_html=True,
            )

        st.divider()

        for row in rows:
            win = row.get("timeframe_label") or label_window(row["simulation_start"], row["simulation_end"])
            ann = row["annualized_return_pct"]
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
            dd = row.get("max_drawdown_pct")
            wr = row.get("win_rate")
            tt = row.get("total_trades")
            parts = []
            if dd is not None and pd.notna(dd):
                parts.append(f"DD {dd:.1f}%")
            if wr is not None and pd.notna(wr):
                parts.append(f"WR {wr*100:.0f}%")
            if tt is not None and pd.notna(tt):
                parts.append(f"{tt} trades")
            if parts:
                st.caption("  ·  ".join(parts))
            if row.get("notes"):
                st.caption(f"💬 {row['notes']}")


# ── Main area ──────────────────────────────────────────────────────────────────

def _render_pending_tabs(pending: list):
    tab_labels = [f"⏳ {label_window(pr['start'], pr['end'])}" for pr in pending]
    tabs = st.tabs(tab_labels)
    for tab, pr in zip(tabs, pending):
        with tab:
            render_model_dashboard(pr["payload"], show_save=True,
                                   key_prefix=f"new_{pr['start']}_{pr['end']}")


if selected_run == "__new__" and latest_run:
    alloc   = latest_run.get("allocations", {})
    lr_ah   = _alloc_hash(alloc)
    pending = _pending_model_for_hash(lr_ah)
    if pending:
        _render_pending_tabs(pending)
    else:
        render_model_dashboard(latest_run, show_save=True, key_prefix="new_run")

elif selected_run is not None and selected_run in run_groups:
    saved_rows   = run_groups[selected_run]
    pending_runs = load_pending_model_runs(saved_rows)

    # ── Classify and order saved entries ────────────────────────────────────
    def _classify_entry(row):
        """Return (tab_label, sort_key, entry_type) for a saved row."""
        notes = (row.get("notes") or "").strip()
        if notes.startswith("regime-robustness:"):
            tag = notes[len("regime-robustness:"):].strip()
            # Year slice: "2019", "2020 YTD", etc.
            if re.match(r"^\d{4}", tag):
                tab_lbl = f"Robust Test - {tag}"
                sort_k  = (2, str(row["simulation_start"]))
            else:
                # Random window: "Random 01 (2020-04-01→2020-09-30)"
                m = re.search(r"Random (\d+)", tag)
                num = m.group(1) if m else "??"
                tab_lbl = f"Rnd {num} — {pd.Timestamp(row['simulation_start']).strftime('%b %Y')}"
                sort_k  = (3, str(row["simulation_start"]))
            return tab_lbl, sort_k, "robust"
        elif pd.notna(row.get("preset_id")):
            lbl    = row.get("timeframe_label", "")
            preset = int(row["preset_id"])
            return lbl, (1, f"{preset:04d}"), "preset"
        else:
            lbl = row.get("timeframe_label") or label_window(
                row["simulation_start"], row["simulation_end"]
            )
            return lbl, (1, lbl), "custom"

    classified = []
    for row in saved_rows:
        lbl, sort_k, etype = _classify_entry(row)
        classified.append({
            "label": lbl, "sort_key": sort_k, "entry_type": etype,
            "type": "saved", "data": row,
        })
    classified.sort(key=lambda e: e["sort_key"])

    # Deduplicate labels
    seen_lbl = {}
    for entry in classified:
        lbl = entry["label"]
        seen_lbl[lbl] = seen_lbl.get(lbl, 0) + 1
        if seen_lbl[lbl] > 1:
            entry["label"] = f"{lbl} ({seen_lbl[lbl]})"

    # Pending runs go at the end
    tab_entries = [{"label": "Overview", "type": "overview"}] + classified
    for pr in pending_runs:
        lbl = label_window(pr["start"], pr["end"])
        tab_entries.append({"label": f"⏳ {lbl}", "type": "pending", "data": pr})

    tabs = st.tabs([e["label"] for e in tab_entries])

    for tab, entry in zip(tabs, tab_entries):
        with tab:
            if entry["type"] == "overview":
                # ── Overview: multi-select + comparative dashboard ─────────
                saved_only = [e for e in classified if e["type"] == "saved"]
                all_labels       = [e["label"] for e in saved_only]
                preset_labels    = [e["label"] for e in saved_only if e["entry_type"] == "preset"]
                robust_labels    = [e["label"] for e in saved_only if e["entry_type"] == "robust"]

                qf_key = f"{selected_run}_qf"
                ms_key = f"{selected_run}_ms"

                # Seed multiselect on first render
                if ms_key not in st.session_state:
                    st.session_state[ms_key] = all_labels

                def _sync_filter():
                    qf = st.session_state[qf_key]
                    if qf == "Presets":
                        st.session_state[ms_key] = preset_labels
                    elif qf == "Robust Tests":
                        st.session_state[ms_key] = robust_labels
                    else:
                        st.session_state[ms_key] = all_labels

                qf_col, ms_col = st.columns([1, 3])
                qf_col.radio(
                    "Quick filter",
                    ["All", "Presets", "Robust Tests"],
                    horizontal=False,
                    key=qf_key,
                    on_change=_sync_filter,
                )
                selected_labels = ms_col.multiselect(
                    "Windows",
                    options=all_labels,
                    key=ms_key,
                )

                selected_entries = [e for e in saved_only if e["label"] in selected_labels]
                overview_data = []
                for e in selected_entries:
                    row = e["data"]
                    overview_data.append({
                        "label":  e["label"],
                        "type":   e["entry_type"],
                        "ann":    row["annualized_return_pct"] if pd.notna(row.get("annualized_return_pct")) else None,
                        "dd":     row["max_drawdown_pct"]      if pd.notna(row.get("max_drawdown_pct"))      else None,
                        "wr":     row["win_rate"]              if pd.notna(row.get("win_rate"))              else None,
                        "trades": row["total_trades"]          if pd.notna(row.get("total_trades"))          else None,
                        "start":  row["simulation_start"],
                        "end":    row["simulation_end"],
                        "notes":  row.get("notes") or "",
                    })

                render_overview_dashboard(overview_data, key_prefix=f"ov_{selected_run}")

            elif entry["type"] == "saved":
                row           = entry["data"]
                model_test_id = int(row["model_test_id"])
                payload       = load_model_run_payload(model_test_id)
                if payload is not None:
                    render_model_dashboard(payload, show_save=False,
                                           key_prefix=f"run_{model_test_id}")
                else:
                    ann = row["annualized_return_pct"]
                    dd  = row["max_drawdown_pct"]
                    wr  = row["win_rate"]
                    _, gl, gc = grade_info(ann if pd.notna(ann) else None)
                    st.markdown(
                        f'<span class="grade-badge" style="background:{gc}22;color:{gc};'
                        f'border:1px solid {gc}66;font-size:1rem;">{gl}</span>',
                        unsafe_allow_html=True,
                    )
                    win = row.get("timeframe_label") or (
                        f"{str(row['simulation_start'])[:10]} → {str(row['simulation_end'])[:10]}"
                    )
                    st.caption(f"{selected_model_version}  ·  {win}")
                    st.divider()
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Annualized Return", f"{ann:+.1f}%" if pd.notna(ann) else "—")
                    c2.metric("Max Drawdown",      f"{dd:.1f}%"   if pd.notna(dd)  else "—")
                    c3.metric("Win Rate",          f"{wr*100:.0f}%" if pd.notna(wr) else "—")
                    st.info("Re-run from Claude Code to restore full charts.")
                    if row.get("notes"):
                        st.caption(f"💬 {row['notes']}")
            else:
                pr = entry["data"]
                render_model_dashboard(pr["payload"], show_save=True,
                                       key_prefix=f"pending_{pr['start']}_{pr['end']}")

else:
    if latest_run:
        tabs = st.tabs(["⏳ Latest Run"])
        with tabs[0]:
            render_model_dashboard(latest_run, show_save=True, key_prefix="latest_new")
    else:
        st.info("No model runs yet. Trigger one from Claude Code using `src/backtester/model_runner.py`.")
        st.code("""
from src.backtester.model_runner import run_model

result = run_model(start="2019-01-01", end="2023-12-31")
print(result)
        """, language="python")
