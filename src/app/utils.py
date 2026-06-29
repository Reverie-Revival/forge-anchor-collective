"""
Pure helpers — no Streamlit UI, no DB calls.
Imported by db.py, dashboard.py, and stream_tester.py.
"""
import hashlib
import json
import pandas as pd
import streamlit as st
import yfinance as yf

SP500_HISTORICAL_AVG = 10.0


@st.cache_data(ttl=3600)
def fetch_sp500(start: str, end: str):
    try:
        df = yf.download("^GSPC", start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return None
        # yfinance returns MultiIndex columns — flatten to get 'Close'
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close  = df["Close"].dropna()
        total  = (close.iloc[-1] / close.iloc[0]) - 1
        years  = (close.index[-1] - close.index[0]).days / 365.25
        ann    = ((1 + total) ** (1 / years) - 1) * 100 if years > 0 else None
        return {"total_return_pct": round(total * 100, 2), "annualized_return_pct": round(ann, 2) if ann else None}
    except Exception:
        return None


def candle_hours(params: dict) -> float:
    return {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}.get(
        params.get("primary_timeframe", "15m"), 0.25
    )


def grade_info(ann):
    if ann is None:
        return None, "—", "#555"
    if ann >= 20:
        return 5, "Grade 5 · Elite",   "#00d4aa"
    if ann >= 10:
        return 4, "Grade 4 · Strong",  "#4ade80"
    if ann >= 8:
        return 3, "Grade 3 · Passing", "#facc15"
    if ann > 0:
        return 2, "Grade 2 · Weak",    "#fb923c"
    return 1, "Grade 1 · Poor", "#f87171"


def _strip_none(obj):
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj


def params_hash(params: dict) -> str:
    return hashlib.md5(json.dumps(_strip_none(params), sort_keys=True).encode()).hexdigest()[:10]


def label_window(start_date, end_date) -> str:
    import pandas as pd
    s = pd.Timestamp(start_date)
    e = pd.Timestamp(end_date)
    if s.year <= 2018 and s.month <= 3:
        return "Full History"
    if s.year >= 2026 and s.month == 1 and s.day == 1:
        return "2026 YTD"
    if s.year == 2019 and e.year <= 2023:
        return "Primary"
    return f"{s.strftime('%b %Y')} → {e.strftime('%b %Y')}"


def _compact_config(params: dict) -> str:
    core      = params.get("core_signal", "")
    core_p    = params.get("core_params") or {}
    pos       = params.get("position") or {}
    tf        = params.get("primary_timeframe") or "15m"
    filters   = params.get("filters") or {}
    sentiment = params.get("sentiment") or {}

    if core == "ema_crossover":
        sig = f"{core_p.get('ema_short')}/{core_p.get('ema_long')} EMA"
    elif core == "rsi_dip":
        sig = f"RSI dip <{core_p.get('rsi_threshold')}"
    elif core == "rsi_recovery":
        sig = f"RSI cross ↑{core_p.get('rsi_threshold')}"
    elif core == "fear_dip":
        sig = f"fear dip {core_p.get('dip_pct')}% below SMA{core_p.get('sma_period','')}"
    elif core == "range_breakout":
        sig = f"{core_p.get('breakout_lookback')}-candle breakout"
    elif core == "volume_surge":
        sig = f"{core_p.get('volume_multiplier')}× vol surge"
    elif core == "sma_pullback":
        sig = f"pullback to {core_p.get('pullback_sma')} SMA"
    else:
        sig = core

    parts  = [f"{sig} · {tf}"]
    active = []
    tc = filters.get("trend_context")
    if tc:
        active.append(f">{tc.get('sma_period')} SMA")
    rsi_f = filters.get("rsi")
    if rsi_f:
        if rsi_f.get("min") is not None: active.append(f"RSI>{rsi_f['min']}")
        if rsi_f.get("max") is not None: active.append(f"RSI<{rsi_f['max']}")
    if filters.get("atr_regime"):
        active.append("low-vol")
    dfh = filters.get("drawdown_from_high")
    if dfh:
        active.append(f"drop≥{dfh.get('min_drop_pct')}%/{dfh.get('lookback_days')}d")
    fg = (sentiment.get("fear_greed") or {})
    if fg.get("max") is not None: active.append(f"F&G<{fg['max']}")
    if fg.get("min") is not None: active.append(f"F&G>{fg['min']}")
    if active:
        parts.append(" · ".join(active))
    trail = pos.get("trailing_stop_pct")
    if trail:
        parts.append(f"{trail}% trail")
    return "  ·  ".join(parts)


def _human_readable_description(params: dict) -> str:
    core      = params.get("core_signal", "")
    core_p    = params.get("core_params") or {}
    filters   = params.get("filters") or {}
    pos       = params.get("position") or {}
    sentiment = params.get("sentiment") or {}
    tf        = params.get("primary_timeframe")

    tf_desc = {
        "1h": "hourly candles (4× less frequent than raw 15m — much less noise)",
        "4h": "4-hour candles (very selective — only catches larger, sustained moves)",
        "1d": "daily candles (only major trend-level shifts trigger an entry)",
    }.get(tf, "15-minute candles (maximum granularity)")

    if core == "ema_crossover":
        s, l = core_p.get("ema_short"), core_p.get("ema_long")
        core_sent = (
            f"Enters when the {s}-period EMA crosses above the {l}-period EMA — "
            f"the short-term trend overtaking the longer-term trend, signaling a momentum shift. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "rsi_dip":
        thresh = core_p.get("rsi_threshold", 35)
        dip    = core_p.get("dip_pct", 2.0)
        smap   = core_p.get("sma_period", 20)
        core_sent = (
            f"Enters when RSI drops below {thresh} (oversold) and price is at least {dip}% "
            f"below its {smap}-period SMA — looking for genuine panic dips likely to bounce. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "range_breakout":
        lb  = core_p.get("breakout_lookback", 48)
        core_sent = (
            f"Enters when price breaks above its highest point over the last {lb} candles — "
            f"the moment a consolidation range gives way. Evaluated on {tf_desc}."
        )
    elif core == "volume_surge":
        mult = core_p.get("volume_multiplier", 2.5)
        core_sent = (
            f"Enters on a volume spike of {mult}× the recent average with a bullish candle. "
            f"Evaluated on {tf_desc}."
        )
    elif core == "rsi_recovery":
        thresh = core_p.get("rsi_threshold", 35)
        extra  = " Price must also close bullish on the entry candle." if core_p.get("require_bullish_candle") else ""
        core_sent = (
            f"Enters the moment RSI crosses back up through {thresh} after being oversold — "
            f"the first sign that a panic dip is reversing. Waits for the turn, not just the dip.{extra} "
            f"Evaluated on {tf_desc}."
        )
    elif core == "fear_dip":
        dip  = core_p.get("dip_pct", 3.0)
        smap = core_p.get("sma_period")
        if smap:
            core_sent = (
                f"Enters when price drops {dip}% below its {smap}-period SMA — a short-term dip "
                f"within a larger regime of fear. No RSI requirement. Evaluated on {tf_desc}."
            )
        else:
            core_sent = (
                f"Enters when price drops {dip}% from the previous candle close — "
                f"a direct price-drop trigger within a fear regime. Evaluated on {tf_desc}."
            )
    elif core == "sma_pullback":
        psma = core_p.get("pullback_sma", 50)
        tol  = core_p.get("pullback_tolerance_pct", 1.5)
        core_sent = (
            f"Enters when price pulls back to within {tol}% of the {psma}-period SMA during an uptrend. "
            f"Evaluated on {tf_desc}."
        )
    else:
        core_sent = f"Core signal: {core}. Evaluated on {tf_desc}."

    filter_sents = []
    tc = filters.get("trend_context")
    if tc:
        req = "above" if tc.get("require", "above") == "above" else "below"
        filter_sents.append(
            f"Only enters when BTC is {req} its {tc.get('sma_period', 200)}-period SMA — "
            f"aligning with the long-term trend."
        )
    rsi_f = filters.get("rsi")
    if rsi_f:
        rsi_parts = []
        if rsi_f.get("min") is not None: rsi_parts.append(f"above {rsi_f['min']} (momentum present)")
        if rsi_f.get("max") is not None: rsi_parts.append(f"below {rsi_f['max']} (not yet overbought)")
        if rsi_parts:
            filter_sents.append(f"RSI must be {' and '.join(rsi_parts)} at entry.")
    if filters.get("atr_regime"):
        max_pct = filters["atr_regime"].get("max_pct_of_avg", 70)
        filter_sents.append(
            f"Requires ATR below {max_pct}% of its recent average — only enters after calm "
            f"consolidation, not chaos."
        )
    fg = (sentiment.get("fear_greed") or {})
    if fg.get("min") is not None:
        filter_sents.append(
            f"Fear & Greed must be above {fg['min']} — only trading when sentiment supports "
            f"momentum, not during panic."
        )
    if fg.get("max") is not None:
        filter_sents.append(
            f"Fear & Greed must be below {fg['max']} — avoiding euphoric markets where momentum "
            f"entries tend to top out."
        )
    dfh = filters.get("drawdown_from_high")
    if dfh:
        filter_sents.append(
            f"Price must have dropped at least {dfh.get('min_drop_pct')}% from its "
            f"{dfh.get('lookback_days')}-day high — only entering after a meaningful crash, "
            f"not a routine dip."
        )

    exit_sents = []
    trail = pos.get("trailing_stop_pct")
    if trail:
        exit_sents.append(
            f"Exits via a {trail}% trailing stop — follows price upward, only triggers if BTC "
            f"drops {trail}% from its peak since entry."
        )
    if pos.get("min_hold_candles"):
        exit_sents.append(
            f"Holds at least {pos['min_hold_candles']} candles before the stop can fire."
        )
    if pos.get("max_hold_candles"):
        exit_sents.append(f"Force-exits after {pos['max_hold_candles']} candles regardless.")

    return " ".join([core_sent] + filter_sents + exit_sents)
