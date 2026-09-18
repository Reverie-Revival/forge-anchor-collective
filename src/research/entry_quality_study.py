"""
Entry-quality research tool (idea #16, docs/ideas.md).

Studies which combinations of indicators predict a good simulated trade
outcome, across the whole BTC/USD candle history -- a systematic
alternative to hand-picking filter combinations per stream the way every
real stream (Volume Raider, Momentum Rider, Dip Hunter, Breakout Scout)
has been designed so far.

THIS IS A RESEARCH TOOL, NOT A LIVE GATE AND NOT A BACKTEST ENGINE
SUBSTITUTE. Its output is a ranked list of feature importances meant to
hand off to a human designing a real stream through the normal pipeline
(Stream Tester -> live-replay backtest -> Gauntlet -> adversarial-code-
review). Never adopt a combination just because this model liked it in
training -- BTC's price history is one long non-stationary time series,
not many independent samples, and a tree given enough features WILL find
in-sample patterns that are pure noise out-of-sample. See docs/ideas.md
#16 for full design rationale and guardrails.

Labeling (Option B from #16): every candle gets a simulated trade using
ONE standardized entry+exit (a fixed trailing stop, real fees) held
constant across every sample -- this isolates "is this entry condition
good" from "which exit mechanic is good," which forward-return labeling
(Option A) would not do.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from src.backtester.market_data import load_market_data
from src.backtester.indicators import (
    resample_ohlcv, rsi, atr, adx, macd, ema, sma, bollinger_bands, volume_sma, add_indicators,
)
from src.backtester.signals import generate_signals
from src.data.sentiment import load_sentiment
from src.fees import MAKER_FEE, TAKER_FEE

_CANDLES_PER_DAY = {"15m": 96, "1h": 24, "4h": 6, "1d": 1}

# Every real core_signal this project has ever used live, with representative
# real core_params pulled from the actual locked configs (VR v1, MR v4, DH v3,
# BS v3 -- see backtest.stream_configs) -- reused so "did this signal fire"
# means the same thing here as it does in a real stream, not a reinvented
# approximation. filters intentionally empty: firing is the PURE core
# pattern; filter-style conditions are separate continuous features below so
# the tree can discover its own combinations instead of inheriting the ones
# already chosen for the live streams.
CORE_SIGNAL_PARAMS = {
    "volume_surge":   {"core_signal": "volume_surge",   "core_params": {"volume_avg_period": 20, "volume_multiplier": 2.0}, "filters": {}},
    "ema_crossover":  {"core_signal": "ema_crossover",  "core_params": {"ema_short": 30, "ema_long": 120}, "filters": {}},
    "rsi_recovery":   {"core_signal": "rsi_recovery",   "core_params": {"rsi_period": 14, "rsi_threshold": 30, "require_bullish_candle": True}, "filters": {}},
    "range_breakout": {"core_signal": "range_breakout", "core_params": {"breakout_lookback": 24}, "filters": {}},
}

FILTER_LEVEL_FEATURES = [
    "rsi_14", "atr_pct", "adx_14", "bb_bandwidth", "volume_ratio",
    "macd_hist", "ema_spread_pct", "dist_from_sma200_pct",
    "atr_percentile_90d", "hour_of_day", "day_of_week", "fng_value", "fng_roc_7d",
]
CORE_SIGNAL_FEATURES = [f"signal_{name}" for name in CORE_SIGNAL_PARAMS]
FEATURE_COLUMNS = FILTER_LEVEL_FEATURES + CORE_SIGNAL_FEATURES


def build_features(df: pd.DataFrame, timeframe: str, sentiment_map: dict = None) -> pd.DataFrame:
    """Add every study feature to df. Expects OHLCV columns, DatetimeIndex."""
    df = df.copy()
    cpd = _CANDLES_PER_DAY.get(timeframe, 24)

    df["rsi_14"] = rsi(df["close"], 14)
    df["atr_14"] = atr(df, 14)
    df["atr_pct"] = df["atr_14"] / df["close"] * 100
    df["adx_14"] = adx(df, 14)

    bb = bollinger_bands(df["close"], 20, 2.0)
    df["bb_bandwidth"] = bb["bb_bandwidth"]

    volume_avg_20 = volume_sma(df["volume"], 20)
    df["volume_ratio"] = df["volume"] / volume_avg_20

    m = macd(df["close"], 12, 26, 9)
    df["macd_hist"] = m["macd_hist"]

    ema_short = ema(df["close"], 12)
    ema_long = ema(df["close"], 26)
    df["ema_spread_pct"] = (ema_short - ema_long) / df["close"] * 100

    sma_200 = sma(df["close"], 200)
    df["dist_from_sma200_pct"] = (df["close"] - sma_200) / sma_200 * 100

    # ATR percentile-of-its-own-trailing-history -- "is volatility high/low
    # relative to how THIS asset has recently behaved," distinct from ADX
    # (trend strength) or raw atr_pct (absolute level).
    window = max(int(90 * cpd), 30)
    df["atr_percentile_90d"] = df["atr_14"].rolling(window).rank(pct=True)

    # bars-since-new-high (idea #11's stagnation metric) -- computed for
    # reference but deliberately EXCLUDED from FEATURE_COLUMNS: the
    # 2026-09-17 walk-forward run found it dominates every fold (61% mean
    # importance) while producing ZERO real out-of-sample predictive power
    # (test sign-accuracy at/below the majority-class baseline in 7/8
    # years) -- it's mechanically entangled with the trailing-stop label
    # itself (a candle far past its own peak is closer to where that
    # trail already triggers), not a discovered trading edge. Left here,
    # not deleted, in case a future non-trailing-stop label wants it.
    running_high = df["close"].cummax()
    is_new_high = df["close"] >= running_high
    df["bars_since_new_high"] = (~is_new_high).groupby(is_new_high.cumsum()).cumcount()

    df["hour_of_day"] = df.index.hour
    df["day_of_week"] = df.index.dayofweek

    if sentiment_map is not None:
        date_series = pd.Series(df.index.date, index=df.index)
        df["fng_value"] = date_series.map(sentiment_map)
        df["fng_roc_7d"] = df["fng_value"].diff(7 * cpd)
    else:
        df["fng_value"] = np.nan
        df["fng_roc_7d"] = np.nan

    # Core-signal firing booleans -- reuses the REAL src/backtester/signals.py
    # generate_signals() for each known core_signal type, with filters={}
    # (pure core pattern; filter-style conditions live as separate
    # continuous features above so the tree can find its own combinations
    # rather than inheriting the ones already chosen for live streams).
    # add_indicators() is called once per signal config so each gets
    # exactly the columns it expects (e.g. ema_short/ema_long at MR's real
    # 30/120 periods for ema_crossover, distinct from this study's own
    # generic 12/26 ema_spread_pct feature above) -- accumulates onto the
    # same df across calls, doesn't clobber earlier columns.
    for name, cfg in CORE_SIGNAL_PARAMS.items():
        df = add_indicators(df, cfg)
        fired = generate_signals(df, cfg)
        df[f"signal_{name}"] = fired.astype(int)

    return df


def simulate_labels(df: pd.DataFrame, trail_pct: float = 8.0, max_hold_candles: int = 500,
                     start_positions: np.ndarray = None) -> pd.Series:
    """
    For each requested start position i, simulate opening a long at
    close[i] and applying a STANDARDIZED trailing stop (trail_pct, real
    MAKER_FEE+TAKER_FEE round-trip) -- held fixed across every sample so
    this isolates entry-condition quality, not exit-mechanic quality.
    Returns net P&L% (fee-inclusive), indexed like df but only populated
    at the requested positions (NaN elsewhere).

    start_positions: integer positions (not labels) to simulate from --
    pass a subsampled set (e.g. one per day) instead of every row to avoid
    training on massively overlapping, near-duplicate trades (consecutive
    hourly starts are nearly the same trade) -- this was silently
    inflating apparent in-sample fit in the first version of this study.
    Defaults to every row if not given.
    """
    close = df["close"].values
    low = df["low"].values
    n = len(close)
    labels = np.full(n, np.nan)
    round_trip_fee_pct = (MAKER_FEE + TAKER_FEE) * 100

    positions = start_positions if start_positions is not None else np.arange(n - 1)

    for i in positions:
        if i >= n - 1:
            continue
        entry_price = close[i]
        hwm = entry_price
        exit_price = None
        end = min(i + 1 + max_hold_candles, n)
        for j in range(i + 1, end):
            c = close[j]
            if c > hwm:
                hwm = c
            stop = hwm * (1 - trail_pct / 100.0)
            if low[j] <= stop:
                exit_price = stop
                break
        if exit_price is None:
            if end - 1 <= i:
                continue
            exit_price = close[end - 1]
        gross_pct = (exit_price - entry_price) / entry_price * 100
        labels[i] = gross_pct - round_trip_fee_pct

    return pd.Series(labels, index=df.index, name="label_pnl_pct")


def _sign_accuracy(y_true, y_pred):
    return float((np.sign(y_true) == np.sign(y_pred)).mean())


def _prepare_study_data(start: str, end: str, timeframe: str, trail_pct: float,
                         max_hold_candles: int, signal_conditioned: bool = True,
                         samples_per_day: int = 1) -> pd.DataFrame:
    """Load, build features, and simulate labels ONCE for the whole range --
    walk-forward folds are then just date-range slices of this, not
    separate reloads/recomputes per fold.

    signal_conditioned=True (the real question this tool exists to answer,
    per the 2026-09-17 reframing): restrict training rows to candles where
    at least one real core_signal actually fired -- a few hundred to low
    thousand genuine historical firings, not an arbitrary sample of any
    random candle. This is what lets the tree consider core_signal
    identity itself as part of "the best combination," not just filter-
    style levels. False falls back to the earlier daily-subsampling mode
    (kept for comparison, not the default)."""
    cpd = _CANDLES_PER_DAY.get(timeframe, 24)
    warmup_days = 250  # covers the 200-period SMA + 90-day ATR percentile window
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    load_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") if end else None

    df_raw = load_market_data(load_start, load_end)
    df = resample_ohlcv(df_raw, timeframe) if timeframe != "15m" else df_raw
    sentiment_map = load_sentiment(load_start, load_end)
    df = build_features(df, timeframe, sentiment_map)

    if signal_conditioned:
        fired_any = (df[CORE_SIGNAL_FEATURES].sum(axis=1) > 0).values
        start_positions = np.where(fired_any)[0]
        start_positions = start_positions[start_positions < len(df) - 1]
    else:
        # Fallback: arbitrary subsampling (default 1/day) instead of every
        # candle -- consecutive hourly starts are nearly-duplicate trades
        # and were silently inflating apparent in-sample fit in the first
        # version of this study (train R^2 0.71 / test R^2 -0.29).
        step = max(int(cpd / samples_per_day), 1)
        start_positions = np.arange(0, len(df) - 1, step)

    df["label_pnl_pct"] = simulate_labels(df, trail_pct, max_hold_candles, start_positions)

    df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]

    return df[FEATURE_COLUMNS + ["label_pnl_pct"]].dropna()


def run_walkforward_study(start: str = "2018-01-01", end: str = None, timeframe: str = "1h",
                           trail_pct: float = 8.0, max_hold_candles: int = 500,
                           signal_conditioned: bool = True, samples_per_day: int = 1,
                           fold_years: list = None) -> dict:
    """
    Yearly expanding-window walk-forward (same shape as the Gauntlet's own
    walk-forward step, feedback_the_gauntlet): train on everything before
    year Y, test on year Y, repeat for each year with data available.
    Reports per-fold train/test performance AND per-fold feature
    importances so importance STABILITY across regimes can be judged
    directly -- an importance that flips between folds is noise, one that
    holds up across bull/bear/chop folds is a real candidate.

    signal_conditioned=True (default): train only on candles where a real
    core_signal fired -- see _prepare_study_data for why this replaced the
    original "every candle" framing.
    """
    study_df = _prepare_study_data(start, end, timeframe, trail_pct, max_hold_candles,
                                    signal_conditioned, samples_per_day)

    years = sorted(study_df.index.year.unique())
    if fold_years is None:
        fold_years = [y for y in years if y > years[0]]  # first year is train-only warmup

    fold_results = []
    for test_year in fold_years:
        train = study_df[study_df.index.year < test_year]
        test = study_df[study_df.index.year == test_year]
        if len(train) < 50 or len(test) < 10:
            continue

        X_train, y_train = train[FEATURE_COLUMNS], train["label_pnl_pct"]
        X_test, y_test = test[FEATURE_COLUMNS], test["label_pnl_pct"]

        model = GradientBoostingRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
        model.fit(X_train, y_train)

        importances = pd.Series(model.feature_importances_, index=FEATURE_COLUMNS)

        fold_results.append({
            "test_year": test_year,
            "n_train": len(train),
            "n_test": len(test),
            "train_r2": model.score(X_train, y_train),
            "test_r2": model.score(X_test, y_test),
            "train_sign_accuracy": _sign_accuracy(y_train, model.predict(X_train)),
            "test_sign_accuracy": _sign_accuracy(y_test, model.predict(X_test)),
            # Naive best-possible baseline: always predict whichever sign is more common in this fold's test set.
            "baseline_sign_accuracy_test": float(max((y_test > 0).mean(), (y_test <= 0).mean())),
            "feature_importances": importances,
        })

    importance_table = pd.DataFrame({f["test_year"]: f["feature_importances"] for f in fold_results})
    importance_table["mean"] = importance_table.mean(axis=1)
    importance_table["std"] = importance_table.drop(columns=["mean"]).std(axis=1)
    importance_table = importance_table.sort_values("mean", ascending=False)

    return {
        "fold_results": fold_results,
        "importance_table": importance_table,
        "n_total_samples": len(study_df),
    }
