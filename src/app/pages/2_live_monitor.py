import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

from src.backtester.market_data import _warmup_days
from src.backtester.indicators import add_indicators, resample_ohlcv
from src.fees import TAKER_FEE

st.title("⚓ Forge Anchor — Live Monitor")

# ── Glossary ──────────────────────────────────────────────────────────────────

GLOSSARY = {
    "HWM (High Water Mark)":
        "The highest price reached since a position opened. The trailing stop is "
        "calculated as N% below this — it moves up as price rises, never down.",
    "Trail Stop":
        "Trailing stop price = HWM × (1 − trail%). If price falls to this level "
        "the position is sold at market. Model 1: active as soon as the position "
        "is open. Model 3 (Grid Stacker): NOT active until price first rises "
        "'Trail Arm %' above average cost — see below.",
    "Trail Arm % (Model 3 only)":
        "Grid Stacker's trailing stop doesn't start protecting a position the "
        "moment it opens — it only arms once price rises this % above the "
        "position's average cost. Below that threshold there is no sell-side "
        "trigger at all; a price drop instead fires the next cascade buy "
        "(see below), not a sale. Once armed, the stop is also floored at "
        "breakeven — it can never voluntarily sell at a real loss.",
    "Cascade Add (Model 3 only)":
        "Grid Stacker averages down on the way down, not just holds: each slot "
        "beyond Slot 1 buys more once price falls a set % below Slot 1's ORIGINAL "
        "entry (not the prior add) — e.g. 1/2/5/10% for a 5-slot ladder. These are "
        "buy triggers, unrelated to the trailing stop above.",
    "Capitulation Stop (Model 3 only)":
        "A backstop that only arms once ALL slots are filled (out of room to "
        "average down further). A further drop below the last fill's price "
        "forces a full exit — the one way this strategy can realize a real loss.",
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
def load_executor_runs(model_id):
    # Model 1's executor.py never sets model_id on its own rows (predates
    # multi-model support) -- Model 3's blended_executor.py does. So "Model 1"
    # rows are the NULL ones, not a model_id=1 match.
    where = "model_id IS NULL" if model_id == 1 else "model_id = :mid"
    rows = _q(f"""
        SELECT run_id, ran_at, last_tick_at, closed_tfs, open_lots, pending_lots,
               signals_fired, entries_placed, fills, expirations, stops_triggered, error
        FROM live.executor_runs
        WHERE {where}
        ORDER BY ran_at DESC
        LIMIT 200
    """, {"mid": model_id})
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
def load_open_lots(model_id):
    rows = _q("""
        SELECT ll.lot_id, ls.stream_name, ll.slot_number, ll.entry_price,
               ll.high_water_mark, ll.btc_quantity, ll.opening_capital,
               ll.opened_at, ll.entry_reason,
               ls.parameters->>'primary_timeframe' AS timeframe
        FROM live.lots ll
        JOIN live.streams ls ON ll.stream_id = ls.stream_id
        WHERE ll.status = 'OPEN' AND ll.model_id = :mid
        ORDER BY ll.opened_at DESC
    """, {"mid": model_id})
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_current_price():
    rows = _q("SELECT close FROM market_data ORDER BY timestamp DESC LIMIT 1")
    return float(rows[0][0]) if rows else None


@st.cache_data(ttl=60)
def load_pending_lots(model_id):
    rows = _q("""
        SELECT ll.lot_id, ls.stream_name, ll.slot_number, ll.entry_price,
               ll.btc_quantity, ll.opening_capital, ll.opened_at, ll.entry_order_id
        FROM live.lots ll
        JOIN live.streams ls ON ll.stream_id = ls.stream_id
        WHERE ll.status = 'PENDING' AND ll.model_id = :mid
        ORDER BY ll.opened_at DESC
    """, {"mid": model_id})
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_closed_lots(model_id):
    rows = _q("""
        SELECT ll.lot_id, ls.stream_name, ll.slot_number,
               ll.entry_price, ll.exit_price, ll.opening_capital,
               ll.closing_capital, ll.realized_pnl, ll.btc_quantity,
               ll.opened_at, ll.closed_at, ll.exit_reason
        FROM live.lots ll
        JOIN live.streams ls ON ll.stream_id = ls.stream_id
        WHERE ll.status = 'CLOSED' AND ll.model_id = :mid
        ORDER BY ll.closed_at DESC
    """, {"mid": model_id})
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_model_info(model_id):
    rows = _q("""
        SELECT model_id, model_version, description, deployed_at, status
        FROM live.models WHERE model_id = :mid
    """, {"mid": model_id})
    return dict(rows[0]._mapping) if rows else {}


@st.cache_data(ttl=60)
def load_blended_positions(model_id, status):
    rows = _q("""
        SELECT bp.position_id, ls.stream_name, bp.status, bp.original_entry_price,
               bp.avg_cost_basis, bp.total_qty, bp.total_deployed, bp.highest_close,
               bp.capitulation_armed, bp.position_capital_base, bp.opened_at,
               bp.pending_entry_expiry_at, bp.pending_add_order_id, bp.pending_add_index,
               bp.pending_add_expiry_at,
               bp.exit_price, bp.closing_capital, bp.realized_pnl, bp.exit_reason, bp.closed_at
        FROM live.blended_positions bp
        JOIN live.streams ls ON bp.stream_id = ls.stream_id
        WHERE bp.model_id = :mid AND bp.status = :status
        ORDER BY bp.created_at DESC
    """, {"mid": model_id, "status": status})
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_blended_stream_params(model_id):
    """cumulative_drop_pcts / slot_count aren't in load_stream_status()'s
    output -- needed separately to compute each cascade slot's trigger price,
    the trailing-stop arm/distance state, and the capitulation backstop."""
    rows = _q("SELECT parameters, slot_count FROM live.streams WHERE model_id = :mid LIMIT 1", {"mid": model_id})
    if not rows:
        return {}
    params, slot_count = rows[0]
    position = params.get("position") or {}
    return {
        "slot_count": int(slot_count),
        "cumulative_drop_pcts": position.get("cumulative_drop_pcts", []),
        "entry_expiry_candles": position.get("entry_expiry_candles"),
        "primary_timeframe": params.get("primary_timeframe", "4h"),
        "trailing_stop_pct": position.get("trailing_stop_pct"),
        "trail_arm_gain_pct": position.get("trail_arm_gain_pct"),
        "capitulation_stop_pct": position.get("capitulation_stop_pct"),
    }


@st.cache_data(ttl=60)
def load_blended_fills(position_ids: tuple):
    if not position_ids:
        return pd.DataFrame()
    rows = _q("""
        SELECT position_id, fill_number, price, capital, qty, order_id, filled_at
        FROM live.blended_fills
        WHERE position_id = ANY(:ids)
        ORDER BY position_id, fill_number
    """, {"ids": list(position_ids)})
    return pd.DataFrame([dict(r._mapping) for r in rows]) if rows else pd.DataFrame()


@st.cache_data(ttl=60)
def load_blended_capital(model_id):
    rows = _q("SELECT available_capital, updated_at FROM live.blended_capital WHERE model_id = :mid", {"mid": model_id})
    return dict(rows[0]._mapping) if rows else {}


@st.cache_data(ttl=60)
def load_stream_status(model_id):
    streams_rows = _q(
        "SELECT stream_id, stream_name, parameters FROM live.streams WHERE model_id = :mid ORDER BY stream_id",
        {"mid": model_id},
    )
    if not streams_rows:
        return []

    now = datetime.now(timezone.utc)
    # Load enough history for the widest warmup any stream here needs, then each
    # stream slices down to its own exact window below -- an EMA's trajectory
    # depends on how far back it starts, so a shared longer window would silently
    # disagree with signal_engine.check() (the real trading decision), which
    # loads exactly _warmup_days(params) + 5 per stream. This bit the Momentum
    # Rider dashboard row 2026-08-06: a real, correctly-fired EMA crossover
    # showed as "not ready" here because this used a hardcoded 70-day window.
    max_warmup_days = max((_warmup_days(dict(sr._mapping)["parameters"]) for sr in streams_rows), default=30) + 5
    cutoff = (now - timedelta(days=max_warmup_days)).strftime("%Y-%m-%d")
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
            # Match signal_engine.check()'s exact window for this stream, not
            # the shared max fetched above -- see comment where max_warmup_days
            # is computed. Truncate to a date string (midnight), same as
            # load_market_data(load_start) does there -- keeping now's time-of-day
            # component here would shift the EMA seed by a few hours, which is
            # enough to flip a razor-thin crossover's sign.
            stream_cutoff = (now - timedelta(days=_warmup_days(params) + 5)).strftime("%Y-%m-%d")
            stream_15m = df_15m[df_15m.index >= stream_cutoff]
            df = resample_ohlcv(stream_15m, tf) if tf != "15m" else stream_15m.copy()
            if params.get("sentiment"):
                df["fng_value"] = [fng_map.get(d) for d in df.index.date]
            df = add_indicators(df, params)

            # Drop the current in-progress candle -- same trimming
            # signal_engine.check() does, so "last" here is always a completed
            # candle, matching what the real trading decision actually saw.
            if tf and len(df) > 1:
                tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(tf, 15)
                candle_duration = timedelta(minutes=tf_minutes)
                df = df[df.index + candle_duration <= now.replace(tzinfo=None)]

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
                if crossed:
                    _p = 1.0
                elif not pd.isna(gap):
                    _p = min(max((gap + 10) / 10 * 0.9, 0.0), 0.9)
                else:
                    _p = 0.0
                conditions.append({
                    "label": f"EMA {core_p['ema_short']}/{core_p['ema_long']} crossover",
                    "current": f"EMA{core_p['ema_short']} ${es:,.0f}  /  EMA{core_p['ema_long']} ${el:,.0f}  ({gap:+.2f}%)",
                    "pass": crossed, "note": note, "progress": _p,
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
                if crossed_up:
                    _p = 1.0
                elif rsi_val < threshold:
                    _p = min(rsi_val / threshold * 0.9, 0.9)
                else:
                    _p = 0.0
                conditions.append({
                    "label": f"RSI recovery (cross above {threshold})",
                    "current": f"RSI {rsi_val:.1f}  (prev {rsi_prev:.1f})",
                    "pass": crossed_up, "note": note, "progress": _p,
                })

            elif core == "range_breakout":
                price = float(last["close"])
                bh = float(last.get("breakout_high", float("nan")))
                gap = (price - bh) / bh * 100 if not pd.isna(bh) and bh else float("nan")
                broke = not pd.isna(bh) and price > bh
                note = "broke out this candle ✓" if broke else f"{abs(gap):.1f}% below breakout level"
                _p = 1.0 if broke else (min(price / bh * 0.95, 0.95) if not pd.isna(bh) and bh > 0 else 0.0)
                conditions.append({
                    "label": f"Range breakout ({core_p.get('breakout_lookback', 24)}-candle high)",
                    "current": f"Price ${price:,.0f}  /  Range High ${bh:,.0f}  ({gap:+.1f}%)",
                    "pass": broke, "note": note, "progress": _p,
                })

            elif core == "fear_dip":
                # Mirrors src/backtester/signals.py's fear_dip check exactly:
                # fires when close drops dip_pct% below either an SMA or the
                # previous candle's close (if no sma_period configured).
                price = float(last["close"])
                dip_pct = core_p.get("dip_pct", 3.0)
                sma_period = core_p.get("sma_period")
                if sma_period and "sma_dip" in last.index and not pd.isna(last["sma_dip"]):
                    baseline = float(last["sma_dip"])
                    baseline_label = f"SMA{sma_period}"
                else:
                    baseline = float(prev["close"])
                    baseline_label = "prev close"
                trigger = baseline * (1 - dip_pct / 100)
                fired = price < trigger
                gap = (price - trigger) / trigger * 100 if trigger else float("nan")
                note = "dipped this candle ✓" if fired else f"{abs(gap):.2f}% above dip trigger"
                _p = 1.0 if fired else (min(trigger / price, 0.95) if price > 0 else 0.0)
                conditions.append({
                    "label": f"Fear dip ({dip_pct}% below {baseline_label})",
                    "current": f"Price ${price:,.0f}  /  Trigger ${trigger:,.0f}  ({gap:+.2f}%)",
                    "pass": fired, "note": note, "progress": _p,
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
                if ok:
                    _p = 1.0
                elif rsi_f.get("min") is not None and rval < rsi_f["min"]:
                    _p = rval / rsi_f["min"]
                elif rsi_f.get("max") is not None and rval > rsi_f["max"]:
                    _p = max(0.0, 1.0 - (rval - rsi_f["max"]) / 30.0)
                else:
                    _p = 0.0
                conditions.append({"label": f"RSI filter ({', '.join(parts)})", "current": f"RSI {rval:.1f}", "pass": ok, "note": "", "progress": _p})

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
                    if ok:
                        _p = 1.0
                    elif req == "above" and sma_val > 0:
                        _p = min(price / sma_val, 0.95)
                    elif req == "below" and price > 0:
                        _p = min(sma_val / price, 0.95)
                    else:
                        _p = 0.0
                    conditions.append({
                        "label": f"Price {req} SMA {tc['sma_period']}",
                        "current": f"${price:,.0f}  /  SMA {tc['sma_period']} ${sma_val:,.0f}  ({gap:+.1f}%)",
                        "pass": ok, "note": "", "progress": _p,
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
                if ok:
                    _p = 1.0
                elif fng_conf.get("min") is not None and latest_fng < fng_conf["min"]:
                    _p = latest_fng / fng_conf["min"]
                elif fng_conf.get("max") is not None and latest_fng > fng_conf["max"]:
                    _p = max(0.0, 1.0 - (latest_fng - fng_conf["max"]) / (100 - fng_conf["max"]))
                else:
                    _p = 0.0
                conditions.append({
                    "label": f"F&G ({', '.join(parts)})",
                    "current": f"F&G {latest_fng} ({latest_fng_date})",
                    "pass": ok, "note": "", "progress": _p,
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
                    "pass": ok, "note": "", "progress": min(abs(dd) / min_drop, 1.0),
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
                    "pass": ok, "note": "", "progress": 1.0 if ok else (min(max_bw / bw, 0.95) if bw > 0 else 0.0),
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
                        "pass": ok, "note": "", "progress": 1.0 if ok else (min(max_pct / ratio, 0.95) if ratio > 0 else 0.0),
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


CENTRAL_TZ = ZoneInfo("America/Chicago")


def _fmt_central(ts):
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "—"
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(CENTRAL_TZ).strftime("%Y-%m-%d %H:%M %Z")


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


def _render_slot_ladder(open_positions, pending_positions, fills, blended_params, current_price, entry_condition):
    """
    Model 3 is one solo stream, but that stream IS 5 weighted cascade slots --
    a single per-stream condition card doesn't show which of the 5 have
    actually filled. Shows each slot's real state: filled (real price/date
    from live.blended_fills), order placed (awaiting fill, real expiry), not
    yet triggered (the actual cumulative_drop_pcts trigger price, and how
    far current price is from it), or -- for slot 1 with no position open
    yet -- the live fear_dip condition progress bar already computed
    elsewhere on this page.
    """
    slot_total = blended_params.get("slot_count", 5)
    cum_drops  = blended_params.get("cumulative_drop_pcts", [])

    stacks = pd.concat([open_positions, pending_positions]) if not (open_positions.empty and pending_positions.empty) else pd.DataFrame()

    if stacks.empty:
        st.caption("No position open. Slot 1 fires on the next entry signal:")
        if entry_condition:
            c = entry_condition
            pct = int(c.get("progress", 1.0 if c["pass"] else 0.0) * 100)
            bar_color = "#4ade80" if c["pass"] else ("#fbbf24" if pct >= 66 else "#f87171")
            icon = "✓" if c["pass"] else "✗"
            st.markdown(
                f"<div style='margin-bottom:8px'>"
                f"<div style='display:flex; justify-content:space-between; align-items:baseline; margin-bottom:4px'>"
                f"<span style='font-size:0.85rem'><span style='color:{bar_color}; font-weight:700'>{icon}</span>&nbsp;Slot 1 — {c['label']}</span>"
                f"<span style='font-size:0.8rem; color:#aaa'>{c['current']}</span>"
                f"</div>"
                f"<div style='background:#2a2a2a; border-radius:4px; height:7px'>"
                f"<div style='background:{bar_color}; width:{pct}%; height:7px; border-radius:4px'></div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
        for n in range(2, slot_total + 1):
            st.caption(f"Slot {n} — waiting on Slot {n - 1} to fill first")
        return

    pos = stacks.iloc[0]
    pos_fills = fills[fills["position_id"] == pos["position_id"]].copy() if not fills.empty else pd.DataFrame()
    original_entry = float(pos["original_entry_price"]) if pd.notna(pos.get("original_entry_price")) else None
    pending_add_idx = int(pos["pending_add_index"]) if pd.notna(pos.get("pending_add_index")) else None
    next_slot_n = len(pos_fills) + 1  # the one slot actually waiting on a trigger right now, not every future one

    for n in range(1, slot_total + 1):
        fill_number = n - 1  # fill_number is 0-indexed in the DB (0 = slot 1)
        fill_row = pos_fills[pos_fills["fill_number"] == fill_number] if not pos_fills.empty else pd.DataFrame()

        c1, c2, c3 = st.columns([1, 2, 4])
        c1.markdown(f"**Slot {n}**")

        if not fill_row.empty:
            f = fill_row.iloc[0]
            c2.markdown("🟢 Filled")
            c3.markdown(
                f"<span style='font-size:0.95rem; font-weight:600'>\\${float(f['price']):,.2f}</span>"
                f"<span style='font-size:0.8rem; color:#888'>&nbsp;&nbsp;·&nbsp;&nbsp;\\${float(f['capital']):,.2f} deployed"
                f"&nbsp;&nbsp;·&nbsp;&nbsp;{_fmt_central(f['filled_at'])}</span>",
                unsafe_allow_html=True,
            )
        elif n == 1 and pos["status"] == "PENDING_ENTRY":
            c2.markdown("🟡 Order Placed")
            c3.caption(f"Awaiting fill, expires {_fmt_central(pos.get('pending_entry_expiry_at'))}")
        elif pending_add_idx is not None and pending_add_idx == fill_number:
            c2.markdown("🟡 Order Placed")
            c3.caption(f"Awaiting fill, expires {_fmt_central(pos.get('pending_add_expiry_at'))}")
        elif n >= 2 and original_entry and len(cum_drops) >= n - 1:
            drop_pct = cum_drops[n - 2]
            trigger_price = original_entry * (1 - drop_pct / 100)
            gap = ((current_price - trigger_price) / trigger_price * 100) if current_price else None

            if n == next_slot_n and gap is not None:
                # This is the one slot actually being watched right now -- give it
                # the same at-a-glance progress bar Slot 1 gets pre-entry, instead
                # of just text, so "how close are we" is visible without reading numbers.
                triggered = gap <= 0
                # Progress toward the trigger: 0% at original entry, 100% at trigger price.
                total_span = original_entry - trigger_price
                progress = 1.0 if triggered else max(0.0, min(1.0, (original_entry - current_price) / total_span)) if total_span > 0 else 0.0
                pct_bar = int(progress * 100)
                bar_color = "#4ade80" if triggered else ("#fbbf24" if pct_bar >= 66 else "#f87171")
                gap_str = "trigger cleared, awaiting next tick" if triggered else f"{gap:+.1f}% away"
                c2.markdown("⚪ Not Triggered")
                c3.markdown(
                    f"<div style='margin-bottom:2px; font-size:0.8rem; color:#aaa'>"
                    f"Needs price ≤ \\${trigger_price:,.2f} ({drop_pct}% below Slot 1) &nbsp;·&nbsp; {gap_str}</div>"
                    f"<div style='background:#2a2a2a; border-radius:4px; height:6px'>"
                    f"<div style='background:{bar_color}; width:{pct_bar}%; height:6px; border-radius:4px'></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                gap_str = f"  ·  {gap:+.1f}% away" if gap is not None and gap > 0 else \
                           ("  ·  trigger cleared, awaiting next tick" if gap is not None else "")
                c2.markdown("⚪ Not Triggered")
                c3.caption(f"Needs price ≤ ${trigger_price:,.2f} ({drop_pct}% below Slot 1){gap_str}")
        else:
            c2.markdown("⚪ Waiting")
            c3.caption("—")

    if pos["capitulation_armed"]:
        st.warning("⚠️ All slots filled — capitulation stop is now armed (no more room to average down).")

    # ── How close is this position to selling? ──────────────────────────────
    # The trailing stop is NOT active until price first rises trail_arm_gain_pct%
    # above avg_cost_basis -- below that threshold there is no sell-side trigger
    # at all (a price drop triggers the next cascade BUY above, not a sale).
    # Showing a naive "HWM x (1-trail%)" number unconditionally (as this page
    # used to) is misleading before that arming point, since no such stop is
    # actually live yet.
    if pos["status"] == "OPEN" and pd.notna(pos.get("avg_cost_basis")) and pd.notna(pos.get("highest_close")):
        avg_cost = float(pos["avg_cost_basis"])
        hwm = float(pos["highest_close"])
        trail_pct = blended_params.get("trailing_stop_pct")
        arm_pct = blended_params.get("trail_arm_gain_pct")
        gain_pct = (hwm - avg_cost) / avg_cost * 100

        st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
        if arm_pct and gain_pct < arm_pct:
            pct_bar = int(max(0.0, min(1.0, gain_pct / arm_pct)) * 100)
            st.caption(
                f"**Selling:** trailing stop not armed yet — needs price {arm_pct}% above avg cost "
                f"(\\${avg_cost:,.2f}) to activate, currently {gain_pct:+.2f}%. Until then a drop only "
                f"triggers the next cascade buy above, it doesn't sell."
            )
            st.markdown(
                f"<div style='background:#2a2a2a; border-radius:4px; height:6px; margin-top:-6px'>"
                f"<div style='background:#60a5fa; width:{pct_bar}%; height:6px; border-radius:4px'></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        elif trail_pct:
            naive_stop = hwm * (1 - trail_pct / 100)
            breakeven = avg_cost / (1 - TAKER_FEE)
            effective_stop = max(naive_stop, breakeven)
            floored = effective_stop > naive_stop
            dist_pct = ((current_price - effective_stop) / effective_stop * 100) if current_price else None
            dist_str = f"  ·  currently {dist_pct:+.1f}% above it" if dist_pct is not None else ""
            floor_note = " (floored at breakeven, never sells at a loss)" if floored else ""
            st.caption(
                f"**Selling:** trailing stop is armed — sells at market if price hits "
                f"\\${effective_stop:,.2f}{floor_note}{dist_str}"
            )


# ── Model selector + refresh ─────────────────────────────────────────────────

MODEL_LABELS = {1: "Model 1", 3: "Model 3"}

col_select, col_refresh = st.columns([6, 1])
with col_select:
    model_choice = st.radio(
        "Model", list(MODEL_LABELS.values()), horizontal=True, label_visibility="collapsed",
    )
with col_refresh:
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

SELECTED_MODEL_ID = {v: k for k, v in MODEL_LABELS.items()}[model_choice]
IS_BLENDED = SELECTED_MODEL_ID == 3   # Model 3 (Grid Stacker Blended) uses live.blended_* tables, not live.lots

# ── Load all data ─────────────────────────────────────────────────────────────

exec_runs        = load_executor_runs(SELECTED_MODEL_ID)
mdata_runs       = load_market_data_runs()   # shared across models -- one BTC feed
model_info       = load_model_info(SELECTED_MODEL_ID)
stream_statuses  = load_stream_status(SELECTED_MODEL_ID)
current_price    = load_current_price()

if IS_BLENDED:
    open_positions    = load_blended_positions(SELECTED_MODEL_ID, "OPEN")
    pending_positions = load_blended_positions(SELECTED_MODEL_ID, "PENDING_ENTRY")
    closed_positions  = load_blended_positions(SELECTED_MODEL_ID, "CLOSED")
    all_position_ids  = tuple(pd.concat([open_positions, pending_positions])["position_id"]) if not (open_positions.empty and pending_positions.empty) else tuple()
    fills             = load_blended_fills(all_position_ids)
    capital_info      = load_blended_capital(SELECTED_MODEL_ID)
    blended_params    = load_blended_stream_params(SELECTED_MODEL_ID)
    open_count        = len(open_positions)
    pending_count     = len(pending_positions) + int((open_positions["pending_add_order_id"].notna()).sum()) if not open_positions.empty else len(pending_positions)
    total_pnl         = closed_positions["realized_pnl"].sum() if not closed_positions.empty else 0.0
else:
    open_lots     = load_open_lots(SELECTED_MODEL_ID)
    pending_lots  = load_pending_lots(SELECTED_MODEL_ID)
    closed_lots   = load_closed_lots(SELECTED_MODEL_ID)
    open_count    = len(open_lots)
    pending_count = len(pending_lots)
    total_pnl     = closed_lots["realized_pnl"].sum() if not closed_lots.empty else 0.0

# ── Section 1: System Status ──────────────────────────────────────────────────

st.markdown('<p class="section-label">System Status</p>', unsafe_allow_html=True)

last_exec   = exec_runs.iloc[0] if not exec_runs.empty else None
last_mdata  = mdata_runs.iloc[0] if not mdata_runs.empty else None

c1, c2, c3, c4, c5 = st.columns(5)

with c1:
    if last_exec is not None:
        dot = _status_dot(last_exec["error"])
        st.metric("Last Executor Run", _ago(last_exec["ran_at"]), delta=f"{dot} {'error' if last_exec['error'] else 'clean'}", delta_color="off")
        st.caption(_fmt_central(last_exec["ran_at"]))
    else:
        st.metric("Last Executor Run", "No data")

with c2:
    if last_mdata is not None:
        dot = _status_dot(last_mdata["error"])
        st.metric("Last Market Data Run", _ago(last_mdata["ran_at"]), delta=f"{dot} {'error' if last_mdata['error'] else 'clean'}", delta_color="off")
        st.caption(_fmt_central(last_mdata["ran_at"]))
    else:
        st.metric("Last Market Data Run", "No data")

with c3:
    st.metric("Open Positions", open_count)

with c4:
    st.metric("Pending Orders", pending_count)

with c5:
    st.metric("Realized P&L", f"${total_pnl:+.2f}")

if model_info:
    deployed = model_info.get("deployed_at")
    deployed_str = _ago(deployed) if deployed else "—"
    st.caption(f"Model {model_info.get('model_version')} · {model_info.get('description')} · deployed {deployed_str}")

if IS_BLENDED and capital_info:
    st.caption(
        f"Compounding capital: \\${float(capital_info['available_capital']):,.2f} "
        f"(started at \\$100) · updated {_fmt_central(capital_info.get('updated_at'))}"
    )

st.divider()

# ── Section 2: Open Positions ─────────────────────────────────────────────────

st.markdown('<p class="section-label">Open Positions</p>', unsafe_allow_html=True)

if not IS_BLENDED:
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

        display["current_price"] = current_price

        cols_show = ["stream_name", "opening_capital", "entry_price", "current_price", "high_water_mark",
                     "trail_stop", "est_pnl_pct", "opened_at"]
        labels = {
            "stream_name": "Stream", "opening_capital": "Capital ($)",
            "entry_price": "Entry Price", "current_price": "Current Price", "high_water_mark": "HWM",
            "trail_stop": "Trail Stop", "est_pnl_pct": "Est. Gain (HWM)",
            "opened_at": "Opened",
        }
        display = display[cols_show].rename(columns=labels)
        display["Opened"] = pd.to_datetime(display["Opened"]).apply(_fmt_central)
        display["Entry Price"] = display["Entry Price"].apply(lambda x: f"${float(x):,.2f}")
        display["Current Price"] = display["Current Price"].apply(lambda x: f"${float(x):,.2f}" if x is not None else "—")
        display["HWM"] = display["HWM"].apply(lambda x: f"${float(x):,.2f}" if x else "—")
        st.dataframe(display, use_container_width=True, hide_index=True)

    if not pending_lots.empty:
        st.caption(f"**{len(pending_lots)} pending order(s) awaiting fill:**")
        p_display = pending_lots[["stream_name", "opening_capital", "entry_price", "opened_at", "entry_order_id"]].copy()
        p_display.columns = ["Stream", "Capital ($)", "Limit Price", "Placed", "Order ID"]
        p_display["Placed"] = pd.to_datetime(p_display["Placed"]).apply(_fmt_central)
        p_display["Limit Price"] = p_display["Limit Price"].apply(lambda x: f"${float(x):,.2f}")
        st.dataframe(p_display, use_container_width=True, hide_index=True)

else:
    # Model 3: one blended stack per stream -- multiple fills rolled into a
    # single weighted-average position, not one row per slot like Model 1.
    stacks = pd.concat([open_positions, pending_positions]) if not (open_positions.empty and pending_positions.empty) else pd.DataFrame()

    if stacks.empty:
        st.caption("No open or pending position.")
    else:
        for _, pos in stacks.iterrows():
            pos_fills = fills[fills["position_id"] == pos["position_id"]].copy() if not fills.empty else pd.DataFrame()
            n_fills = len(pos_fills)

            hc1, hc2, hc3 = st.columns([3, 4, 3])
            with hc1:
                st.markdown(f"**{pos['stream_name']}**")
                st.caption(f"{pos['status']}  ·  {n_fills} slot(s) filled")
            with hc2:
                if pos["status"] == "PENDING_ENTRY":
                    st.caption(f"Awaiting slot-1 fill, expires {_fmt_central(pos['pending_entry_expiry_at'])}")
                else:
                    avg_cost = float(pos["avg_cost_basis"]) if pd.notna(pos["avg_cost_basis"]) else None
                    st.caption(
                        f"Avg Entry \\${avg_cost:,.2f}  ·  Current \\${current_price:,.2f}"
                        if avg_cost and current_price else "—"
                    )
                    if pd.notna(pos["pending_add_order_id"]):
                        st.caption(f"Cascade add pending, expires {_fmt_central(pos['pending_add_expiry_at'])}")
            with hc3:
                capital_base = float(pos["position_capital_base"]) if pd.notna(pos["position_capital_base"]) else None
                st.caption(f"Capital base: ${capital_base:,.2f}" if capital_base else "—")
                if pos["capitulation_armed"]:
                    st.caption("⚠️ Capitulation armed (out of slots)")

            if pos["status"] == "OPEN" and pd.notna(pos["highest_close"]):
                # Real armed/not-armed trailing-stop distance is shown in the
                # Slot Status section below (_render_slot_ladder) -- this used
                # to show an unconditional "HWM x (1-trail%)" number here too,
                # which is misleading before the trail actually arms (see there).
                st.caption(f"HWM \\${float(pos['highest_close']):,.2f}")

            if not pos_fills.empty:
                with st.expander(f"Fills ({n_fills})"):
                    f_display = pos_fills.copy()
                    f_display["Filled"] = f_display["filled_at"].apply(_fmt_central)
                    f_display["Price"] = f_display["price"].apply(lambda x: f"${float(x):,.2f}")
                    f_display["Capital ($)"] = f_display["capital"].apply(lambda x: f"${float(x):,.2f}")
                    f_display = f_display[["fill_number", "Price", "Capital ($)", "qty", "Filled"]]
                    f_display.columns = ["Slot", "Price", "Capital ($)", "BTC Qty", "Filled"]
                    st.dataframe(f_display, use_container_width=True, hide_index=True)
            st.divider()

st.divider()

# ── Section 3: Stream Status / Slot Status ────────────────────────────────────

st.markdown(
    f'<p class="section-label">{"Slot Status" if IS_BLENDED else "Stream Status"}</p>',
    unsafe_allow_html=True,
)

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
            ts_str = _fmt_central(ts)
            st.caption(f"Last candle: {ts_str}  ·  BTC ${ss['last_close']:,.2f}")
        with hc3:
            label = "🟢 signal firing" if all_pass else "conditions met"
            st.markdown(
                f"<div style='text-align:right; font-size:1.4rem; font-weight:700; color:{color}'>"
                f"{n_met}/{n_total}</div>"
                f"<div style='text-align:right; font-size:0.75rem; color:#888'>{label}</div>",
                unsafe_allow_html=True,
            )

        if IS_BLENDED:
            entry_condition = ss["conditions"][0] if ss["conditions"] else None
            _render_slot_ladder(open_positions, pending_positions, fills, blended_params, current_price, entry_condition)
        else:
            for c in ss["conditions"]:
                pct = int(c.get("progress", 1.0 if c["pass"] else 0.0) * 100)
                bar_color = "#4ade80" if c["pass"] else ("#fbbf24" if pct >= 66 else "#f87171")
                icon = "✓" if c["pass"] else "✗"
                note_part = f" <span style='color:#666; font-size:0.75rem'>— {c['note']}</span>" if c.get("note") else ""
                st.markdown(
                    f"<div style='margin-bottom:12px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:baseline; margin-bottom:4px'>"
                    f"<span style='font-size:0.85rem'><span style='color:{bar_color}; font-weight:700'>{icon}</span>&nbsp;{c['label']}{note_part}</span>"
                    f"<span style='font-size:0.8rem; color:#aaa'>{c['current']}</span>"
                    f"</div>"
                    f"<div style='background:#2a2a2a; border-radius:4px; height:7px'>"
                    f"<div style='background:{bar_color}; width:{pct}%; height:7px; border-radius:4px'></div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
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
    display["Ran At"] = pd.to_datetime(display["ran_at"]).apply(_fmt_central)
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
                ran = _fmt_central(pd.to_datetime(row["ran_at"]))
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
    display["Ran At"] = pd.to_datetime(display["ran_at"]).apply(_fmt_central)
    display["Candles Fetched"] = display["candles_fetched"].fillna("—")
    display["Latest Candle"] = display["latest_candle"].apply(_fmt_central)
    display["Error"] = display["error"].fillna("")

    cols = ["Status", "Ran At", "Candles Fetched", "Latest Candle", "Error"]
    st.dataframe(display[cols], use_container_width=True, hide_index=True,
                 column_config={"Error": st.column_config.TextColumn(width="large")})

st.divider()

# ── Section 5: Closed Trades ──────────────────────────────────────────────────

st.markdown('<p class="section-label">Closed Trades</p>', unsafe_allow_html=True)

if not IS_BLENDED:
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
        display["Opened"] = pd.to_datetime(display["opened_at"]).apply(_fmt_central)
        display["Closed"] = pd.to_datetime(display["closed_at"]).apply(_fmt_central)
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
        summary_pnl = closed_lots["realized_pnl"].sum()

        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Total Trades", total_trades)
        sc2.metric("Win Rate", f"{win_rate:.0f}%  ({winners}/{total_trades})")
        sc3.metric("Total Realized P&L", f"${summary_pnl:+.2f}")

else:
    if closed_positions.empty:
        st.caption("No closed positions yet.")
    else:
        display = closed_positions.copy()
        display["P&L"] = display["realized_pnl"].apply(
            lambda x: f"${float(x):+.2f}" if pd.notna(x) else "—"
        )
        display["Return"] = display.apply(
            lambda r: f"{(float(r['realized_pnl']) / float(r['position_capital_base']) * 100):+.2f}%"
            if pd.notna(r["realized_pnl"]) and pd.notna(r["position_capital_base"]) and float(r["position_capital_base"]) > 0 else "—", axis=1
        )
        display["Avg Entry"] = display["avg_cost_basis"].apply(lambda x: f"${float(x):,.2f}" if pd.notna(x) else "—")
        display["Exit Price"] = display["exit_price"].apply(lambda x: f"${float(x):,.2f}" if pd.notna(x) else "—")
        display["Capital Base ($)"] = display["position_capital_base"].apply(lambda x: f"${float(x):,.2f}" if pd.notna(x) else "—")
        display["Opened"] = pd.to_datetime(display["opened_at"]).apply(_fmt_central)
        display["Closed"] = pd.to_datetime(display["closed_at"]).apply(_fmt_central)
        display["Hold"] = (
            pd.to_datetime(display["closed_at"]) - pd.to_datetime(display["opened_at"])
        ).apply(lambda d: f"{int(d.total_seconds() // 3600)}h" if pd.notna(d) else "—")

        cols_show = ["stream_name", "Capital Base ($)", "Avg Entry", "Exit Price",
                     "P&L", "Return", "Opened", "Closed", "Hold", "exit_reason"]
        labels = {"stream_name": "Stream", "exit_reason": "Exit Reason"}
        display = display[cols_show].rename(columns=labels)
        st.dataframe(display, use_container_width=True, hide_index=True)

        # Summary row
        total_trades = len(closed_positions)
        winners = (closed_positions["realized_pnl"] > 0).sum()
        win_rate = winners / total_trades * 100 if total_trades > 0 else 0
        summary_pnl = closed_positions["realized_pnl"].sum()

        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Total Positions", total_trades)
        sc2.metric("Win Rate", f"{win_rate:.0f}%  ({winners}/{total_trades})")
        sc3.metric("Total Realized P&L", f"${summary_pnl:+.2f}")
