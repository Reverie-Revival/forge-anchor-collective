import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

from src.backtester.indicators import add_indicators, resample_ohlcv

st.title("⚓ Forge Anchor — Live Monitor")

# ── Glossary ──────────────────────────────────────────────────────────────────

GLOSSARY = {
    "HWM (High Water Mark)":
        "The highest price reached since a position opened. The trailing stop is "
        "calculated as N% below this — it moves up as price rises, never down.",
    "Trail Stop":
        "Trailing stop price = HWM × (1 − trail%). If price falls to this level "
        "the position is sold at market.",
    "TFs Closed":
        "Timeframes Closed — the candle timeframes (e.g. 1h, 4h) that completed "
        "during an executor run. The executor only acts on a stream when its candle "
        "has closed.",
    "F&G (Fear & Greed)":
        "Fear & Greed Index (0–100). Extreme Fear = 0–24, Fear = 25–49, "
        "Neutral = 50, Greed = 51–74, Extreme Greed = 75–100. Used as an "
        "entry filter by all three streams.",
    "EMA":
        "Exponential Moving Average — weights recent prices more heavily. "
        "Momentum Rider fires when the fast EMA (30) crosses above the slow EMA "
        "(120) on the 4h chart.",
    "RSI":
        "Relative Strength Index (0–100). Measures momentum. Below 30 = oversold, "
        "above 70 = overbought. Dip Hunter enters when RSI recovers above 30 from "
        "an oversold level.",
    "BB Bandwidth":
        "Bollinger Band Bandwidth — how wide the price bands are relative to price. "
        "A low value means a volatility squeeze. Breakout Scout requires a squeeze "
        "before entering to catch the expansion move.",
    "ATR":
        "Average True Range — measures candle-level volatility. Breakout Scout "
        "requires ATR to be below 90% of its 30-candle average (calm conditions).",
    "Entries":
        "New limit buy orders placed during the run.",
    "Fills":
        "Pending limit orders confirmed filled (PENDING → OPEN).",
    "Expires":
        "Pending orders cancelled because they weren't filled within the expiry window.",
    "Stops":
        "OPEN positions where the trailing stop triggered and a market sell was placed.",
}

with st.expander("📖 Glossary"):
    cols = st.columns(2)
    for i, (term, defn) in enumerate(GLOSSARY.items()):
        cols[i % 2].markdown(f"**{term}**  \n{defn}")


# ── DB connection (Supabase only) ─────────────────────────────────────────────

@st.cache_resource
def _get_engine():
    url = os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    if not url:
        st.error("SUPABASE_DATABASE_URL not set.")
        st.stop()
    if "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _q(sql, params=None):
    with _get_engine().connect() as conn:
        result = conn.execute(text(sql), params or {})
        return result.fetchall()


# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_executor_runs():
    rows = _q("""
        SELECT run_id, ran_at, last_tick_at, closed_tfs, open_lots, pending_lots,
               signals_fired, entries_placed, fills, expirations, stops_triggered, error
        FROM live.executor_runs
        ORDER BY ran_at DESC
        LIMIT 200
    """)
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_market_data_runs():
    rows = _q("""
        SELECT run_id, ran_at, candles_fetched, latest_candle, error
        FROM live.market_data_runs
        ORDER BY ran_at DESC
        LIMIT 200
    """)
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_open_lots():
    rows = _q("""
        SELECT ll.lot_id, ls.stream_name, ll.slot_number, ll.entry_price,
               ll.high_water_mark, ll.btc_quantity, ll.opening_capital,
               ll.opened_at, ll.entry_reason,
               ls.parameters->>'primary_timeframe' AS timeframe
        FROM live.lots ll
        JOIN live.streams ls ON ll.stream_id = ls.stream_id
        WHERE ll.status = 'OPEN'
        ORDER BY ll.opened_at DESC
    """)
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_pending_lots():
    rows = _q("""
        SELECT ll.lot_id, ls.stream_name, ll.slot_number, ll.entry_price,
               ll.btc_quantity, ll.opening_capital, ll.opened_at, ll.entry_order_id
        FROM live.lots ll
        JOIN live.streams ls ON ll.stream_id = ls.stream_id
        WHERE ll.status = 'PENDING'
        ORDER BY ll.opened_at DESC
    """)
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_closed_lots():
    rows = _q("""
        SELECT ll.lot_id, ls.stream_name, ll.slot_number,
               ll.entry_price, ll.exit_price, ll.opening_capital,
               ll.closing_capital, ll.realized_pnl, ll.btc_quantity,
               ll.opened_at, ll.closed_at, ll.exit_reason
        FROM live.lots ll
        JOIN live.streams ls ON ll.stream_id = ls.stream_id
        WHERE ll.status = 'CLOSED'
        ORDER BY ll.closed_at DESC
    """)
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_model_info():
    rows = _q("""
        SELECT model_id, model_version, description, deployed_at, status
        FROM live.models WHERE status = 'active' LIMIT 1
    """)
    return dict(rows[0]._mapping) if rows else {}


@st.cache_data(ttl=60)
def load_stream_status():
    streams_rows = _q("SELECT stream_id, stream_name, parameters FROM live.streams ORDER BY stream_id")
    if not streams_rows:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=70)).strftime("%Y-%m-%d")
    mdata_rows = _q(
        "SELECT timestamp, open, high, low, close, volume FROM market_data "
        "WHERE timestamp >= :c ORDER BY timestamp",
        {"c": cutoff},
    )
    if not mdata_rows:
        return []

    df_15m = pd.DataFrame([dict(r._mapping) for r in mdata_rows])
    df_15m["timestamp"] = pd.to_datetime(df_15m["timestamp"])
    df_15m = df_15m.set_index("timestamp").sort_index()
    if df_15m.index.tz is not None:
        df_15m.index = df_15m.index.tz_localize(None)

    sent_rows = _q("SELECT date, fng_value FROM sentiment_data ORDER BY date DESC LIMIT 90")
    fng_map = {r[0]: int(r[1]) for r in sent_rows} if sent_rows else {}
    latest_fng = int(sent_rows[0][1]) if sent_rows else None
    latest_fng_date = sent_rows[0][0] if sent_rows else None

    results = []
    for sr in streams_rows:
        stream = dict(sr._mapping)
        params = stream["parameters"]
        tf = params.get("primary_timeframe", "15m")
        try:
            df = resample_ohlcv(df_15m, tf) if tf != "15m" else df_15m.copy()
            if params.get("sentiment"):
                df["fng_value"] = [fng_map.get(d) for d in df.index.date]
            df = add_indicators(df, params)
            if len(df) < 2:
                results.append({"stream_name": stream["stream_name"], "error": "insufficient data"})
                continue

            last = df.iloc[-1]
            prev = df.iloc[-2]
            last_ts = df.index[-1]
            core = params.get("core_signal")
            core_p = params.get("core_params", {})
            filters = params.get("filters") or {}
            sentiment_conf = params.get("sentiment") or {}
            conditions = []

            if core == "ema_crossover":
                es = float(last.get("ema_short", float("nan")))
                el = float(last.get("ema_long", float("nan")))
                gap = (es - el) / el * 100 if el else float("nan")
                crossed = (not pd.isna(prev.get("ema_short"))) and (
                    prev["ema_short"] <= prev["ema_long"] and last["ema_short"] > last["ema_long"]
                )
                if gap > 0:
                    note = "crossed this candle ✓" if crossed else "aligned — awaiting next cross"
                else:
                    note = f"{abs(gap):.2f}% below — gap closing" if gap > -2 else "below — not aligned"
                conditions.append({
                    "label": f"EMA {core_p['ema_short']}/{core_p['ema_long']} crossover",
                    "current": f"EMA{core_p['ema_short']} ${es:,.0f}  /  EMA{core_p['ema_long']} ${el:,.0f}  ({gap:+.2f}%)",
                    "pass": crossed, "note": note,
                })

            elif core == "rsi_recovery":
                rsi_val = float(last.get("rsi", float("nan")))
                rsi_prev = float(prev.get("rsi", float("nan")))
                threshold = core_p.get("rsi_threshold", 35)
                crossed_up = rsi_prev < threshold and rsi_val >= threshold
                if crossed_up:
                    note = "crossed this candle ✓"
                elif rsi_val < threshold:
                    note = f"oversold ({rsi_val:.1f}) — watching for bounce"
                else:
                    note = f"above threshold — wait for next dip below {threshold}"
                conditions.append({
                    "label": f"RSI recovery (cross above {threshold})",
                    "current": f"RSI {rsi_val:.1f}  (prev {rsi_prev:.1f})",
                    "pass": crossed_up, "note": note,
                })

            elif core == "range_breakout":
                price = float(last["close"])
                bh = float(last.get("breakout_high", float("nan")))
                gap = (price - bh) / bh * 100 if not pd.isna(bh) and bh else float("nan")
                broke = not pd.isna(bh) and price > bh
                note = "broke out this candle ✓" if broke else f"{abs(gap):.1f}% below breakout level"
                conditions.append({
                    "label": f"Range breakout ({core_p.get('breakout_lookback', 24)}-candle high)",
                    "current": f"Price ${price:,.0f}  /  Range High ${bh:,.0f}  ({gap:+.1f}%)",
                    "pass": broke, "note": note,
                })

            # RSI filter
            rsi_f = filters.get("rsi") or {}
            if rsi_f and "rsi" in last.index and not pd.isna(last["rsi"]):
                rval = float(last["rsi"])
                ok = True
                parts = []
                if rsi_f.get("min") is not None:
                    ok = ok and rval >= rsi_f["min"]
                    parts.append(f"≥ {rsi_f['min']}")
                if rsi_f.get("max") is not None:
                    ok = ok and rval <= rsi_f["max"]
                    parts.append(f"≤ {rsi_f['max']}")
                conditions.append({"label": f"RSI filter ({', '.join(parts)})", "current": f"RSI {rval:.1f}", "pass": ok, "note": ""})

            # Trend context (SMA)
            tc = filters.get("trend_context") or {}
            if tc:
                col = f"trend_sma_{tc['sma_period']}"
                if col in last.index and not pd.isna(last[col]):
                    sma_val = float(last[col])
                    price = float(last["close"])
                    req = tc.get("require", "above")
                    ok = (price > sma_val) if req == "above" else (price < sma_val)
                    gap = (price - sma_val) / sma_val * 100
                    conditions.append({
                        "label": f"Price {req} SMA {tc['sma_period']}",
                        "current": f"${price:,.0f}  /  SMA {tc['sma_period']} ${sma_val:,.0f}  ({gap:+.1f}%)",
                        "pass": ok, "note": "",
                    })

            # Sentiment / F&G
            fng_conf = sentiment_conf.get("fear_greed") or {}
            if fng_conf and latest_fng is not None:
                ok = True
                parts = []
                if fng_conf.get("min") is not None:
                    ok = ok and latest_fng >= fng_conf["min"]
                    parts.append(f"≥ {fng_conf['min']}")
                if fng_conf.get("max") is not None:
                    ok = ok and latest_fng <= fng_conf["max"]
                    parts.append(f"≤ {fng_conf['max']}")
                conditions.append({
                    "label": f"F&G ({', '.join(parts)})",
                    "current": f"F&G {latest_fng} ({latest_fng_date})",
                    "pass": ok, "note": "",
                })

            # Drawdown from high
            dfh_f = filters.get("drawdown_from_high") or {}
            if dfh_f and "drawdown_from_high_pct" in last.index and not pd.isna(last["drawdown_from_high_pct"]):
                dd = float(last["drawdown_from_high_pct"])
                min_drop = dfh_f.get("min_drop_pct", 15.0)
                ok = dd <= -min_drop
                conditions.append({
                    "label": f"Drawdown from {dfh_f.get('lookback_days', 90)}d high ≥ {min_drop}%",
                    "current": f"{dd:.1f}% from peak",
                    "pass": ok, "note": "",
                })

            # Bollinger squeeze
            bb_f = filters.get("bollinger") or {}
            if bb_f and "bb_bandwidth" in last.index and not pd.isna(last["bb_bandwidth"]):
                bw = float(last["bb_bandwidth"])
                max_bw = (bb_f.get("squeeze") or {}).get("max_bandwidth_pct", 6.0)
                ok = bw <= max_bw
                conditions.append({
                    "label": f"BB squeeze (bandwidth ≤ {max_bw}%)",
                    "current": f"BB bandwidth {bw:.1f}%",
                    "pass": ok, "note": "",
                })

            # ATR regime
            atr_f = filters.get("atr_regime") or {}
            if atr_f and "atr" in last.index and "atr_avg" in last.index:
                if not pd.isna(last["atr"]) and not pd.isna(last["atr_avg"]) and float(last["atr_avg"]) > 0:
                    ratio = float(last["atr"]) / float(last["atr_avg"]) * 100
                    max_pct = atr_f.get("max_pct_of_avg", 90)
                    ok = ratio <= max_pct
                    conditions.append({
                        "label": f"ATR regime (≤ {max_pct}% of avg)",
                        "current": f"ATR {ratio:.0f}% of avg",
                        "pass": ok, "note": "",
                    })

            n_met = sum(1 for c in conditions if c["pass"])
            results.append({
                "stream_name": stream["stream_name"],
                "stream_id": stream["stream_id"],
                "timeframe": tf,
                "core_signal": core,
                "last_close": float(last["close"]),
                "last_candle_ts": last_ts,
                "conditions": conditions,
                "conditions_met": n_met,
                "conditions_total": len(conditions),
            })
        except Exception as e:
            results.append({"stream_name": stream["stream_name"], "error": str(e)})

    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ago(ts):
    if ts is None:
        return "—"
    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _status_dot(error):
    return "🔴" if error else "🟢"


def _fmt_tfs(tfs):
    if not tfs:
        return "—"
    return ", ".join(sorted(tfs))


def _fmt_signals(signals):
    if not signals:
        return "—"
    return ", ".join(signals)


def _pnl_color(val):
    if val is None:
        return ""
    return "color: #4ade80" if val > 0 else ("color: #f87171" if val < 0 else "")


# ── Refresh button ────────────────────────────────────────────────────────────

col_title, col_refresh = st.columns([6, 1])
with col_refresh:
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── Load all data ─────────────────────────────────────────────────────────────

exec_runs     = load_executor_runs()
mdata_runs    = load_market_data_runs()
open_lots     = load_open_lots()
pending_lots  = load_pending_lots()
closed_lots   = load_closed_lots()
model_info    = load_model_info()
stream_statuses = load_stream_status()

# ── Section 1: System Status ──────────────────────────────────────────────────

st.markdown('<p class="section-label">System Status</p>', unsafe_allow_html=True)

last_exec   = exec_runs.iloc[0] if not exec_runs.empty else None
last_mdata  = mdata_runs.iloc[0] if not mdata_runs.empty else None

c1, c2, c3, c4, c5 = st.columns(5)

with c1:
    if last_exec is not None:
        dot = _status_dot(last_exec["error"])
        st.metric("Last Executor Run", _ago(last_exec["ran_at"]), delta=f"{dot} {'error' if last_exec['error'] else 'clean'}", delta_color="off")
    else:
        st.metric("Last Executor Run", "No data")

with c2:
    if last_mdata is not None:
        dot = _status_dot(last_mdata["error"])
        st.metric("Last Market Data Run", _ago(last_mdata["ran_at"]), delta=f"{dot} {'error' if last_mdata['error'] else 'clean'}", delta_color="off")
    else:
        st.metric("Last Market Data Run", "No data")

with c3:
    st.metric("Open Positions", len(open_lots))

with c4:
    st.metric("Pending Orders", len(pending_lots))

with c5:
    total_pnl = closed_lots["realized_pnl"].sum() if not closed_lots.empty else 0.0
    st.metric("Realized P&L", f"${total_pnl:+.2f}")

if model_info:
    deployed = model_info.get("deployed_at")
    deployed_str = _ago(deployed) if deployed else "—"
    st.caption(f"Model {model_info.get('model_version')} · {model_info.get('description')} · deployed {deployed_str}")

st.divider()

# ── Section 2: Open Positions ─────────────────────────────────────────────────

st.markdown('<p class="section-label">Open Positions</p>', unsafe_allow_html=True)

if open_lots.empty:
    st.caption("No open positions.")
else:
    display = open_lots.copy()
    # Compute trail stop and unrealized P&L estimate using last known HWM
    display["trail_stop"] = None
    display["est_pnl_pct"] = None

    rows = _q("""
        SELECT stream_name, parameters->'position'->>'trailing_stop_pct' AS trail_pct
        FROM live.streams
    """)
    trail_map = {r[0]: float(r[1]) for r in rows if r[1]}

    for idx, row in display.iterrows():
        hwm = float(row["high_water_mark"] or row["entry_price"])
        trail = trail_map.get(row["stream_name"])
        if trail:
            display.at[idx, "trail_stop"] = f"${hwm * (1 - trail/100):,.2f}  ({trail}% below HWM ${hwm:,.0f})"
        ep = float(row["entry_price"])
        display.at[idx, "est_pnl_pct"] = f"{((hwm - ep) / ep * 100):+.2f}% (HWM)"

    cols_show = ["stream_name", "opening_capital", "entry_price", "high_water_mark",
                 "trail_stop", "est_pnl_pct", "opened_at"]
    labels = {
        "stream_name": "Stream", "opening_capital": "Capital ($)",
        "entry_price": "Entry Price", "high_water_mark": "HWM",
        "trail_stop": "Trail Stop", "est_pnl_pct": "Est. Gain (HWM)",
        "opened_at": "Opened",
    }
    display = display[cols_show].rename(columns=labels)
    display["Opened"] = pd.to_datetime(display["Opened"]).dt.strftime("%Y-%m-%d %H:%M UTC")
    display["Entry Price"] = display["Entry Price"].apply(lambda x: f"${float(x):,.2f}")
    display["HWM"] = display["HWM"].apply(lambda x: f"${float(x):,.2f}" if x else "—")
    st.dataframe(display, use_container_width=True, hide_index=True)

if not pending_lots.empty:
    st.caption(f"**{len(pending_lots)} pending order(s) awaiting fill:**")
    p_display = pending_lots[["stream_name", "opening_capital", "entry_price", "opened_at", "entry_order_id"]].copy()
    p_display.columns = ["Stream", "Capital ($)", "Limit Price", "Placed", "Order ID"]
    p_display["Placed"] = pd.to_datetime(p_display["Placed"]).dt.strftime("%Y-%m-%d %H:%M UTC")
    p_display["Limit Price"] = p_display["Limit Price"].apply(lambda x: f"${float(x):,.2f}")
    st.dataframe(p_display, use_container_width=True, hide_index=True)

st.divider()

# ── Section 3: Stream Status ──────────────────────────────────────────────────

st.markdown('<p class="section-label">Stream Status</p>', unsafe_allow_html=True)

if not stream_statuses:
    st.caption("No stream data available.")
else:
    for ss in stream_statuses:
        if "error" in ss:
            st.warning(f"{ss['stream_name']}: {ss['error']}")
            continue

        n_met = ss["conditions_met"]
        n_total = ss["conditions_total"]
        all_pass = n_met == n_total and n_total > 0
        color = "#4ade80" if all_pass else ("#fbbf24" if n_met >= n_total / 2 else "#f87171")

        hc1, hc2, hc3 = st.columns([3, 5, 2])
        with hc1:
            st.markdown(f"**{ss['stream_name']}**")
            st.caption(f"{ss['timeframe']} · {ss['core_signal'].replace('_', ' ')}")
        with hc2:
            ts = ss.get("last_candle_ts")
            ts_str = pd.to_datetime(ts).strftime("%Y-%m-%d %H:%M UTC") if ts is not None else "—"
            st.caption(f"Last candle: {ts_str}  ·  BTC ${ss['last_close']:,.2f}")
        with hc3:
            label = "🟢 signal firing" if all_pass else "conditions met"
            st.markdown(
                f"<div style='text-align:right; font-size:1.4rem; font-weight:700; color:{color}'>"
                f"{n_met}/{n_total}</div>"
                f"<div style='text-align:right; font-size:0.75rem; color:#888'>{label}</div>",
                unsafe_allow_html=True,
            )

        cdf = pd.DataFrame([
            {"": "✓" if c["pass"] else "✗", "Condition": c["label"], "Current": c["current"], "Note": c["note"]}
            for c in ss["conditions"]
        ])
        st.dataframe(cdf, use_container_width=True, hide_index=True,
                     column_config={"": st.column_config.TextColumn(width="small")})
        st.divider()

# ── Section 4: Executor Run Log ───────────────────────────────────────────────

st.markdown('<p class="section-label">Executor Run Log</p>', unsafe_allow_html=True)

if exec_runs.empty:
    st.caption("No executor runs recorded yet.")
else:
    display = exec_runs.copy()
    show_all_exec = st.checkbox("Show all (up to last 200 runs)", key="exec_show_all", value=False)
    if not show_all_exec:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
        ran_col = pd.to_datetime(display["ran_at"])
        if ran_col.dt.tz is None:
            ran_col = ran_col.dt.tz_localize("UTC")
        display = display[ran_col >= cutoff]

    display["Status"] = display["error"].apply(lambda e: "🔴 error" if e else "🟢 clean")
    display["Ran At"] = pd.to_datetime(display["ran_at"]).dt.strftime("%Y-%m-%d %H:%M UTC")
    display["TFs Closed"] = display["closed_tfs"].apply(_fmt_tfs)
    display["Signals"] = display["signals_fired"].apply(_fmt_signals)
    display["Open"] = display["open_lots"].fillna("—")
    display["Pending"] = display["pending_lots"].fillna("—")
    display["Entries"] = display["entries_placed"].fillna(0).astype(int)
    display["Fills"] = display["fills"].fillna(0).astype(int)
    display["Expires"] = display["expirations"].fillna(0).astype(int)
    display["Stops"] = display["stops_triggered"].fillna(0).astype(int)
    display["Error"] = display["error"].fillna("")

    cols = ["Status", "Ran At", "TFs Closed", "Open", "Pending",
            "Signals", "Entries", "Fills", "Expires", "Stops", "Error"]
    st.dataframe(display[cols], use_container_width=True, hide_index=True,
                 column_config={"Error": st.column_config.TextColumn(width="large")})

    exec_errors = exec_runs[exec_runs["error"].notna()]
    if not exec_errors.empty:
        with st.expander(f"⚠️ {len(exec_errors)} run(s) with errors"):
            for _, row in exec_errors.iterrows():
                ran = pd.to_datetime(row["ran_at"]).strftime("%Y-%m-%d %H:%M UTC")
                st.code(f"{ran}\n{row['error']}")

st.divider()

# ── Section 4: Market Data Run Log ────────────────────────────────────────────

st.markdown('<p class="section-label">Market Data Run Log</p>', unsafe_allow_html=True)

if mdata_runs.empty:
    st.caption("No market data runs recorded yet.")
else:
    display = mdata_runs.copy()
    show_all_mdata = st.checkbox("Show all (up to last 200 runs)", key="mdata_show_all", value=False)
    if not show_all_mdata:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
        ran_col = pd.to_datetime(display["ran_at"])
        if ran_col.dt.tz is None:
            ran_col = ran_col.dt.tz_localize("UTC")
        display = display[ran_col >= cutoff]
    display["Status"] = display["error"].apply(lambda e: "🔴 error" if e else "🟢 clean")
    display["Ran At"] = pd.to_datetime(display["ran_at"]).dt.strftime("%Y-%m-%d %H:%M UTC")
    display["Candles Fetched"] = display["candles_fetched"].fillna("—")
    display["Latest Candle"] = display["latest_candle"].apply(
        lambda x: pd.to_datetime(x).strftime("%Y-%m-%d %H:%M UTC") if pd.notna(x) else "—"
    )
    display["Error"] = display["error"].fillna("")

    cols = ["Status", "Ran At", "Candles Fetched", "Latest Candle", "Error"]
    st.dataframe(display[cols], use_container_width=True, hide_index=True,
                 column_config={"Error": st.column_config.TextColumn(width="large")})

st.divider()

# ── Section 5: Closed Trades ──────────────────────────────────────────────────

st.markdown('<p class="section-label">Closed Trades</p>', unsafe_allow_html=True)

if closed_lots.empty:
    st.caption("No closed trades yet.")
else:
    display = closed_lots.copy()
    display["P&L"] = display["realized_pnl"].apply(
        lambda x: f"${float(x):+.2f}" if pd.notna(x) else "—"
    )
    display["Return"] = display.apply(
        lambda r: f"{(float(r['realized_pnl']) / float(r['opening_capital']) * 100):+.2f}%"
        if pd.notna(r["realized_pnl"]) and float(r["opening_capital"]) > 0 else "—", axis=1
    )
    display["Entry Price"] = display["entry_price"].apply(lambda x: f"${float(x):,.2f}")
    display["Exit Price"] = display["exit_price"].apply(lambda x: f"${float(x):,.2f}" if pd.notna(x) else "—")
    display["Opened"] = pd.to_datetime(display["opened_at"]).dt.strftime("%m-%d %H:%M")
    display["Closed"] = pd.to_datetime(display["closed_at"]).dt.strftime("%m-%d %H:%M")
    display["Hold"] = (
        pd.to_datetime(display["closed_at"]) - pd.to_datetime(display["opened_at"])
    ).apply(lambda d: f"{int(d.total_seconds() // 3600)}h" if pd.notna(d) else "—")

    cols_show = ["stream_name", "opening_capital", "Entry Price", "Exit Price",
                 "P&L", "Return", "Opened", "Closed", "Hold", "exit_reason"]
    labels = {
        "stream_name": "Stream", "opening_capital": "Capital ($)",
        "exit_reason": "Exit Reason",
    }
    display = display[cols_show].rename(columns=labels)
    st.dataframe(display, use_container_width=True, hide_index=True)

    # Summary row
    total_trades = len(closed_lots)
    winners = (closed_lots["realized_pnl"] > 0).sum()
    win_rate = winners / total_trades * 100 if total_trades > 0 else 0
    total_pnl = closed_lots["realized_pnl"].sum()

    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("Total Trades", total_trades)
    sc2.metric("Win Rate", f"{win_rate:.0f}%  ({winners}/{total_trades})")
    sc3.metric("Total Realized P&L", f"${total_pnl:+.2f}")
