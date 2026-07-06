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
    RUNS_DIR,
    load_streams, load_stream_configs, load_stream_history, load_timeframe_presets,
    load_run_payload, save_stream_test,
)
from src.app.dashboard import render_dashboard
from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold

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
        "Each stream can run in 1–3 slots. 'single' = one position at a time. "
        "'staggered' = independent slots consume signals round-robin (longest-free first). "
        "'scale_down' = slot 2 adds if price drops further (DH). "
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
    st.markdown("#### Slot Position")
    st.caption(
        "Controls how capital is deployed across multiple independent slots within a stream. "
        "Each slot maintains its own position, capital, and trailing stop independently. "
        "Slot config lives under a `slots` key in the stream's parameters."
    )
    slot_rows = [
        ("**slot_count**",              "Number of independent slots (1–3)",
         "all modes", "1"),
        ("**slot_mode**",               "How slots interact: `single` · `staggered` · `scale_down` · `scale_up`",
         "all modes", "single"),
        ("**slot_entry_gap_candles**",  "Minimum candles between any two slot entries — prevents rapid stacking",
         "staggered", "0"),
        ("**slot2_trigger_pct**",       "Price must move this % from slot 1's entry before slot 2 fires",
         "scale_up / scale_down", "—"),
        ("**slot_capital_weight**",     "Capital split across slots, e.g. `[70, 30]` — sums to 100. "
         "Default is equal split.",
         "multi-slot", "equal split"),
    ]
    c1, c2, c3, c4 = st.columns([1.8, 2.8, 1.2, 1.0])
    c1.markdown("**Parameter**"); c2.markdown("**What it does**")
    c3.markdown("**Applies to**"); c4.markdown("**Default**")
    st.divider()
    for name, desc, applies, default in slot_rows:
        c1, c2, c3, c4 = st.columns([1.8, 2.8, 1.2, 1.0])
        c1.markdown(name); c2.markdown(desc)
        c3.markdown(applies); c4.markdown(default)

    st.markdown("---")
    st.markdown("#### Timeframe")
    st.markdown(
        "**primary_timeframe** — candle size for signal evaluation. "
        "Options: `15m` · `1h` · `4h` · `1d`. "
        "Raw data is always 15m candles; resampling happens at run time. "
        "Coarser timeframes fire less often but with much less noise."
    )


# ── Load streams + configs ────────────────────────────────────────────────────

all_streams = load_streams()
presets     = load_timeframe_presets()

if not all_streams:
    st.info("No streams in the database yet.")
    st.stop()

stream_names = [s["stream_name"] for s in all_streams]
stream_by_name = {s["stream_name"]: s for s in all_streams}


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Stream Tester")

    st.markdown('<p class="config-group-header">Stream</p>', unsafe_allow_html=True)
    selected_stream_name = st.selectbox("", stream_names, label_visibility="collapsed")

    selected_stream = stream_by_name[selected_stream_name]
    stream_id       = selected_stream["stream_id"]
    configs         = load_stream_configs(stream_id)

    if not configs:
        st.info("No configs for this stream yet.")
        st.stop()

    st.markdown('<p class="config-group-header" style="margin-top:14px;">Config Version</p>',
                unsafe_allow_html=True)

    # Build config options: show version + best annualized return across all presets
    def _config_label(cfg: dict) -> str:
        history = load_stream_history(cfg["stream_config_id"])
        if not history.empty and history["annualized_return_pct"].notna().any():
            best = history["annualized_return_pct"].dropna().max()
            n    = len(history)
            return f"{cfg['version']}  ·  {best:+.1f}% best  ·  {n} run{'s' if n != 1 else ''}"
        return cfg["version"]

    config_options = [c["stream_config_id"] for c in configs]
    config_labels  = {c["stream_config_id"]: _config_label(c) for c in configs}

    selected_config_id = st.selectbox(
        "", config_options,
        index=len(config_options) - 1,
        format_func=lambda x: config_labels.get(x, str(x)),
        label_visibility="collapsed",
    )

    selected_config = next(c for c in configs if c["stream_config_id"] == selected_config_id)

    # Config details panel
    st.divider()
    st.markdown(f"**{selected_stream_name} {selected_config['version']}**")

    params_dict = selected_config["params"]
    description = _human_readable_description(params_dict)
    st.markdown(f"*{description}*")

    with st.expander("Signal details"):
        st.caption(_compact_config(params_dict))

    slot_count = selected_config["slot_count"]
    slot_mode  = selected_config["slot_mode"]
    slot_info  = f"{slot_count} slot{'s' if slot_count > 1 else ''} · {slot_mode}"
    if slot_count > 1:
        weights = params_dict.get("slots", {}).get("slot_capital_weight")
        if weights:
            slot_info += f" · [{', '.join(str(w) for w in weights)}]%"
    st.caption(f"⚙ {slot_info}")
    st.divider()

    # Per-preset result badges
    history = load_stream_history(selected_config_id)
    preset_map = {p["preset_id"]: p["name"] for p in presets}

    saved_preset_ids = set()
    if not history.empty and "preset_id" in history.columns:
        saved_preset_ids = set(history["preset_id"].dropna().astype(int).tolist())

    for p in presets:
        pid = p["preset_id"]
        if pid in saved_preset_ids:
            row = history[history["preset_id"] == pid].iloc[0]
            ann = row["annualized_return_pct"]
            _, gl, gc = grade_info(ann if pd.notna(ann) else None)
            st.markdown(
                f'<span class="grade-badge" style="background:{gc}20;color:{gc};'
                f'border:1px solid {gc}55;font-size:0.73rem;padding:2px 8px;">'
                f'✓ {p["name"]}  ·  {ann:+.1f}%</span>' if pd.notna(ann) else
                f'<span class="grade-badge" style="background:#33333380;color:#aaa;'
                f'border:1px solid #55555555;font-size:0.73rem;padding:2px 8px;">'
                f'✓ {p["name"]}</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<span class="grade-badge" style="background:#22222280;color:#666;'
                f'border:1px solid #44444455;font-size:0.73rem;padding:2px 8px;">'
                f'○ {p["name"]}</span>',
                unsafe_allow_html=True,
            )

    st.divider()

    run_all = st.button("▶ Run All Presets", use_container_width=True, type="primary")


# ── Run All Presets ──────────────────────────────────────────────────────────

def _run_and_save(cfg: dict, preset: dict, initial_capital: float = 20.0) -> dict:
    """Run one backtest for a stream config + preset, save to DB, return the payload."""
    p = cfg["params"]
    result = run_backtest(
        params       = p,
        start        = str(preset["start_date"]),
        end          = str(preset["end_date"]) if preset.get("end_date") else None,
        slot_count   = cfg["slot_count"],
        slot_mode    = cfg["slot_mode"],
        stream_name  = cfg["stream_name"],
        lot_size_usd = initial_capital,
    )
    metrics = compute_metrics(result["trades"], initial_capital, result["start"], result["end"])
    ending  = initial_capital + (metrics["total_pnl"] or 0)
    bh      = btc_buy_and_hold(result["df"], initial_capital)

    payload = {
        "stream_name":      cfg["stream_name"],
        "stream_config_id": cfg["stream_config_id"],
        "params":           p,
        "result":           result,
        "trades":           result["trades"],
        "df":               result["df"],
        "metrics":          metrics,
        "bh":               bh,
        "initial_capital":  initial_capital,
        "ending_balance":   ending,
        "slot_count":       cfg["slot_count"],
        "slot_mode":        cfg["slot_mode"],
        "lot_size_usd":     initial_capital,
    }
    save_stream_test(
        stream_config_id = cfg["stream_config_id"],
        params           = p,
        result           = result,
        metrics          = metrics,
        initial_capital  = initial_capital,
        ending_balance   = ending,
        payload          = payload,
        preset_id        = preset["preset_id"],
    )
    return payload


if run_all:
    missing = [p for p in presets if p["preset_id"] not in saved_preset_ids]
    if not missing:
        st.success("All presets already saved for this config.")
    else:
        progress = st.progress(0, text=f"Running {len(missing)} preset(s)…")
        for i, preset in enumerate(missing):
            progress.progress(i / len(missing), text=f"Running {preset['name']}…")
            try:
                _run_and_save(selected_config, preset)
            except Exception as e:
                st.error(f"Failed on {preset['name']}: {e}")
        progress.progress(1.0, text="Done.")
        st.cache_data.clear()
        st.rerun()


# ── Main area ─────────────────────────────────────────────────────────────────

# Build tab list: presets first, then any custom runs from DB
custom_rows = []
if not history.empty:
    custom_rows = history[history["preset_id"].isna()].to_dict("records")

tab_entries = []
for p in presets:
    pid = p["preset_id"]
    existing = None
    if not history.empty and pid in saved_preset_ids:
        match = history[history["preset_id"] == pid]
        if not match.empty:
            existing = match.iloc[0].to_dict()
    tab_entries.append({
        "label":    p["name"],
        "preset":   p,
        "existing": existing,
        "type":     "preset",
    })

for row in custom_rows:
    lbl = label_window(
        row.get("simulation_start") or row.get("custom_start"),
        row.get("simulation_end")   or row.get("custom_end"),
    )
    tab_entries.append({
        "label":    f"⊕ {lbl}",
        "existing": row,
        "type":     "custom",
    })

if not tab_entries:
    st.info("No presets configured. Add presets in the database to run tests.")
    st.stop()

tabs = st.tabs([e["label"] for e in tab_entries])

for tab, entry in zip(tabs, tab_entries):
    with tab:
        existing = entry.get("existing")

        if existing is not None:
            test_id = int(existing["test_id"])
            payload = load_run_payload(test_id)

            if payload is not None:
                render_dashboard(payload, show_save=False,
                                 key_prefix=f"cfg_{selected_config_id}_t{test_id}")
            else:
                # No pkl — show DB summary
                ann = existing.get("annualized_return_pct")
                pf  = existing.get("profit_factor")
                dd  = existing.get("max_drawdown_pct")
                wr  = existing.get("win_rate")
                _, gl, gc = grade_info(ann if pd.notna(ann) else None)
                st.markdown(
                    f'<span class="grade-badge" style="background:{gc}22;color:{gc};'
                    f'border:1px solid {gc}66;font-size:1rem;">{gl}</span>',
                    unsafe_allow_html=True,
                )
                sim_s = str(existing.get("simulation_start") or "")[:10]
                sim_e = str(existing.get("simulation_end")   or "")[:10]
                st.caption(
                    f"{selected_stream_name} {selected_config['version']}  ·  {sim_s} → {sim_e}"
                )
                st.divider()
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Annualized Return", f"{ann:+.1f}%" if pd.notna(ann) else "—")
                c2.metric("Profit Factor",     f"{pf:.2f}"    if pd.notna(pf)  else "—")
                c3.metric("Max Drawdown",      f"{dd:.1f}%"   if pd.notna(dd)  else "—")
                c4.metric("Win Rate",          f"{wr*100:.0f}%" if pd.notna(wr) else "—")

                # Re-run button for missing pkl
                if entry["type"] == "preset" and st.button(
                    "↺ Re-run to restore charts", key=f"rerun_{test_id}"
                ):
                    with st.spinner("Running…"):
                        try:
                            payload = _run_and_save(selected_config, entry["preset"])
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        else:
            # Not run yet
            st.markdown(
                '<div style="color:#666; font-size:0.9rem; padding:24px 0">'
                'This preset has not been run yet for this config.'
                '</div>',
                unsafe_allow_html=True,
            )
            if entry["type"] == "preset" and st.button(
                f"▶ Run {entry['preset']['name']}", key=f"run_{entry['preset']['preset_id']}"
            ):
                with st.spinner(f"Running {entry['preset']['name']}…"):
                    try:
                        _run_and_save(selected_config, entry["preset"])
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
