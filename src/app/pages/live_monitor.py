import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

st.title("⚓ Forge Anchor — Live Monitor")


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

exec_runs   = load_executor_runs()
mdata_runs  = load_market_data_runs()
open_lots   = load_open_lots()
pending_lots = load_pending_lots()
closed_lots = load_closed_lots()
model_info  = load_model_info()

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

# ── Section 3: Executor Run Log ───────────────────────────────────────────────

st.markdown('<p class="section-label">Executor Run Log</p>', unsafe_allow_html=True)

if exec_runs.empty:
    st.caption("No executor runs recorded yet.")
else:
    display = exec_runs.copy()

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
