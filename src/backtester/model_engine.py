"""
Model-level backtest engine.
Runs all locked streams simultaneously with their configured allocations and aggregates results.
"""
import os
import pickle
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from .engine import run_backtest
from .live_replay_stream import run_live_replay_stream
from .market_data import load_market_data
from .indicators import add_indicators
from .metrics import compute_metrics, btc_buy_and_hold
from src.fees import MAKER_FEE, TAKER_FEE

# Same runs/ directory src/app/db.py's save_stream_test already pickles every
# stream test's full payload (including raw trades) into -- reused here
# read-only so this stays DB-layer-agnostic like engine.py's own
# load_market_data, rather than importing src/app/db.py and pulling in its
# Streamlit dependency for a pure backtest module.
_RUNS_DIR = Path(__file__).parent.parent / "app" / "runs"


def _get_local_engine():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "forge_anchor")
        user = os.getenv("DB_USER", "")
        pwd  = os.getenv("DB_PASSWORD", "")
        auth = f"{user}:{pwd}@" if user else ""
        db_url = f"postgresql+psycopg2://{auth}{host}:{port}/{name}"
    elif db_url.startswith("postgresql://") and "+psycopg2" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(db_url)


def _cached_stream_trades(stream_config_id: int, start: str, end: str):
    """A saved stream_tests result's (trades, capital_it_was_tested_at) for
    this exact (stream_config_id, start, end), reused instead of a fresh
    run_backtest() call -- docs/decisions/009 item #7. save_stream_test
    already pickles the full trades DataFrame into runs/{test_id}.pkl for
    every stream test ever saved (stream_tester.py's payload["trades"]);
    this just looks it up by matching either a custom-range row directly, or
    a preset row whose timeframe_presets dates equal the requested (start,
    end) -- the same two dedup shapes save_stream_test itself supports.

    The capital is returned alongside the trades, not baked in, because a
    stream-locking test's capital (CLAUDE.md: "a placeholder until model
    assembly") is very often NOT the same dollar amount a model later
    allocates that stream -- confirmed this session running both paths
    side by side for Model 1: identical trade counts, different $ pnl,
    because the locked test ran at its own placeholder lot_size_usd, not
    Model 1's real $33.33 allocation. The caller must rescale pnl/capital
    to its own lot_size_usd before using these trades for anything -- see
    run_model_backtest below.

    None if nothing on record matches, in which case the caller must run
    fresh (and non-pooled model assembly then still gets a fresh, uncached
    run -- this is a cache, not a requirement).
    """
    try:
        engine = _get_local_engine()
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT test_id FROM backtest.stream_tests st
                WHERE st.stream_config_id = :cid
                  AND (
                    (st.custom_start = :start AND st.custom_end IS NOT DISTINCT FROM :end)
                    OR st.preset_id IN (
                        SELECT preset_id FROM timeframe_presets
                        WHERE start_date = :start AND end_date IS NOT DISTINCT FROM :end
                    )
                  )
                ORDER BY st.saved_at DESC LIMIT 1
            """), {"cid": stream_config_id, "start": start, "end": end}).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    path = _RUNS_DIR / f"{row[0]}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        return None
    trades = payload.get("trades")
    cached_capital = payload.get("lot_size_usd") or payload.get("initial_capital")
    if trades is None or not cached_capital:
        return None
    return trades, float(cached_capital)


def run_model_backtest(stream_configs: list, start: str = None, end: str = None,
                       maker_fee: float = MAKER_FEE, taker_fee: float = TAKER_FEE,
                       use_cache: bool = False) -> dict:
    """
    Run a combined model backtest across all streams.

    stream_configs: list of dicts, each with:
        stream_id    (int)
        stream_name  (str)   — full name including version, e.g. "Momentum Rider v1"
        params       (dict)  — backtest params from backtest.streams
        lot_size_usd (float) — capital per slot
        slot_count   (int)   — max concurrent positions for this stream
        slot_mode    (str)   — 'single' | 'staggered' | 'scale_down' | 'scale_up' |
            'cascade' | 'blended' — only single/staggered go through live-replay
            (see use_cache below); the rest fall back to engine.py's run_backtest.
        stream_config_id (int, optional) — enables cache reuse below; without
            it, this stream is always re-simulated fresh.

    maker_fee/taker_fee default to the real current Kraken rate (src/fees.py) --
    override to re-run history under a hypothetical rate without editing code.

    use_cache: reuse an already-saved stream_tests trade result for a stream
        whose (stream_config_id, start, end) exactly matches one on record,
        instead of doing a fresh run for it -- docs/decisions/009 item #7.
        Streams don't interact in this (non-pooled) combiner, so IN
        PRINCIPLE a cached, already-validated stream's trades are exactly
        what a fresh run would produce (rescaled for capital -- see
        _cached_stream_trades). Defaults to **False** -- opt-in only.
        Confirmed this session with a real example (Model 1's Breakout Scout
        v2, test_id 172): the cache key is only (stream_config_id, start,
        end) with no tie to what version of engine.py/live_replay_stream.py
        computed it, so a `stream_tests` row saved before a real bug fix
        (here: the 2026-08-07 unconditional-compounding fix) is served
        forever as if still valid -- nothing currently re-runs or
        invalidates it when the underlying logic changes. Safe to opt into
        for fast exploratory iteration during stream/model design (the "why
        are we re-running this" case this was built for) as long as you know
        the streams involved haven't gone stale; NOT safe as the source of a
        number that gates a real deployment decision -- those must always
        come from a fresh run, same as before this flag existed.

    Returns a combined payload dict with per-stream results and aggregated metrics.
    """
    stream_results = []
    all_trade_frames = []
    reference_df = None

    for sc in stream_configs:
        # Blended mode's lot_size_usd IS the stream's total capital pool (split
        # across slots internally via slot_capital_weight) -- unlike every other
        # slot_mode, where lot_size_usd is per-slot and gets multiplied up.
        if sc.get("slot_mode") == "blended":
            initial_capital = sc["lot_size_usd"]
        else:
            initial_capital = sc["lot_size_usd"] * sc["slot_count"]

        cached = None
        if use_cache and sc.get("stream_config_id") is not None:
            cached = _cached_stream_trades(sc["stream_config_id"], start, end)

        if cached is not None:
            cached_trades, cached_capital = cached
            # A stream-locking test's capital is a placeholder (CLAUDE.md),
            # very often not the model's real allocation for this stream --
            # rescale pnl/capital to this model's actual lot_size_usd (%
            # gain per trade is capital-independent, only the $ amount
            # scales) rather than trusting the cached $ values as-is.
            trades = cached_trades.copy()
            if not trades.empty and cached_capital > 0:
                scale = sc["lot_size_usd"] / cached_capital
                if scale != 1.0:
                    trades["capital"] = trades["capital"] * scale
                    trades["pnl"] = trades["pnl"] * scale
            result = {
                "trades": trades, "df": None, "start": pd.Timestamp(start),
                "end": pd.Timestamp(end) if end else pd.Timestamp.now(),
                "signals": pd.Series(dtype=bool), "maker_fee": maker_fee, "taker_fee": taker_fee,
            }
        else:
            slot_mode = sc.get("slot_mode", "single")
            # live_replay_stream drives the REAL order_manager code, which
            # always uses the real current MAKER_FEE/TAKER_FEE (src/fees.py)
            # -- it has no hypothetical-fee-override hook the way run_backtest
            # does, so a caller asking for a non-default rate (or a slot_mode
            # live has no parity for -- blended/cascade/scale_down/scale_up)
            # falls back to engine.py, same as before this session.
            if slot_mode in ("single", "staggered") and maker_fee == MAKER_FEE and taker_fee == TAKER_FEE:
                result = run_live_replay_stream(
                    sc["params"],
                    start=start,
                    end=end,
                    slot_count=sc["slot_count"],
                    slot_mode=slot_mode,
                    stream_name=sc["stream_name"],
                    lot_size_usd=sc["lot_size_usd"],
                )
            else:
                result = run_backtest(
                    sc["params"],
                    start=start,
                    end=end,
                    slot_count=sc["slot_count"],
                    slot_mode=slot_mode,
                    stream_name=sc["stream_name"],
                    lot_size_usd=sc["lot_size_usd"],
                    maker_fee=maker_fee,
                    taker_fee=taker_fee,
                )
        trades  = result["trades"]
        metrics = compute_metrics(trades, initial_capital, result["start"], result["end"])
        ending_balance = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)

        if reference_df is None and result["df"] is not None:
            reference_df = result["df"]

        if not trades.empty:
            t = trades.copy()
            t["stream_name"] = sc["stream_name"]
            t["stream_id"]   = sc["stream_id"]
            all_trade_frames.append(t)

        # Drop the raw OHLCV dataframe before storing -- nothing downstream
        # reads it back, and it multiplies with stream count (each stream
        # would otherwise carry its own full-history copy into the saved pkl).
        result_light = {k: v for k, v in result.items() if k != "df"}
        result_light["signals"] = int(result["signals"].sum())

        stream_results.append({
            "stream_id":        sc["stream_id"],
            "stream_config_id": sc.get("stream_config_id"),
            "stream_name":      sc["stream_name"],
            "lot_size_usd":    sc["lot_size_usd"],
            "slot_count":      sc["slot_count"],
            "slot_mode":       sc.get("slot_mode", "single"),
            "initial_capital": initial_capital,
            "ending_balance":  ending_balance,
            "result":          result_light,
            "trades":          trades,
            "metrics":         metrics,
        })

    total_capital = sum(sr["initial_capital"] for sr in stream_results)
    period_start  = stream_results[0]["result"]["start"] if stream_results else pd.Timestamp(start)
    period_end    = stream_results[-1]["result"]["end"]   if stream_results else pd.Timestamp(end)

    if all_trade_frames:
        combined_trades = (
            pd.concat(all_trade_frames)
            .sort_values("entry_ts")
            .reset_index(drop=True)
        )
    else:
        combined_trades = pd.DataFrame()

    combined_metrics = compute_metrics(combined_trades, total_capital, period_start, period_end)
    bh = btc_buy_and_hold(reference_df, total_capital) if reference_df is not None else {}

    return {
        "stream_results":   stream_results,
        "combined_trades":  combined_trades,
        "combined_metrics": combined_metrics,
        "total_capital":    total_capital,
        "bh":               bh,
        "start":            period_start,
        "end":              period_end,
        "maker_fee":        maker_fee,
        "taker_fee":        taker_fee,
    }


def run_pooled_model_backtest(
    stream_configs: list,
    start: str = None,
    end: str = None,
    dip_threshold_pct: float = 15.0,
    sell_premium_pct: float = 50.0,
    dynamic_skim: dict = None,
    min_buy_capital: float = 10.0,
    starting_pool: float = None,
    maker_fee: float = MAKER_FEE,
    taker_fee: float = TAKER_FEE,
) -> dict:
    """
    Model-level pooled-reserve backtest — see docs/decisions/008. Single-slot
    streams only (slot_count == 1, slot_mode == 'single'); Model 1/2's real
    composition. Each stream's own trailing-stop/entry-signal timing is
    unaffected by capital size (% based, not $ based -- true throughout this
    codebase), so each stream is first backtested normally at its full
    lot_size_usd via run_backtest() to get real (entry_ts, exit_ts, roi)
    triples; this function then re-derives dollar P&L by walking all streams'
    entries/exits in true chronological order against ONE shared cash pool:

    - pool >= baseline (sum of configured lot_size_usd): stream trades at its
      full configured size, unchanged from compound=False.
    - pool < baseline: a stream's entry size shrinks to pool * (its own
      lot_size_usd / baseline) -- proportional to its designed weight, not an
      equal split.
    - computed size < $10 (CLAUDE.md's hard minimum): that signal is skipped
      entirely for that stream -- no trade, no pool effect. It resumes the
      next time its own share clears $10 (kept simple, no extra cooldown).

    Skimming into the BTC bucket only touches the portion of a winning
    trade's gain that pushes the pool ABOVE baseline (the "surplus") --
    money that rebuilds the pool back toward baseline is never skimmed.
    dynamic_skim's rate formula (target_trades/avg_win_pct/min/max_skim_pct,
    same tuning as docs/decisions/007) is applied to that surplus portion
    only, referencing the pool's current size (not a single stream's).

    Bucket buy/sell mechanics (dip entry, principal-recovery exit, house
    money) are unchanged from the original simulate_skim_bucket
    (docs/decisions/007 section 4, recovered from commit bb18d45) -- just fed
    by this pooled skim timeline instead of one stream's own trades.
    """
    for sc in stream_configs:
        if sc.get("slot_count", 1) != 1 or sc.get("slot_mode", "single") != "single":
            raise ValueError(
                f"run_pooled_model_backtest only supports single-slot streams "
                f"(got {sc['stream_name']}: slot_count={sc.get('slot_count')}, "
                f"slot_mode={sc.get('slot_mode')}) -- see docs/decisions/008."
            )

    baseline_total = sum(sc["lot_size_usd"] for sc in stream_configs)

    # Pass 1: each stream's own real trade timing/returns, independent of pool state.
    events = []  # (ts, seq, kind, stream_idx, trade_idx)
    per_stream_trades = []
    for si, sc in enumerate(stream_configs):
        result = run_backtest(
            sc["params"], start=start, end=end, slot_count=1, slot_mode="single",
            stream_name=sc["stream_name"], lot_size_usd=sc["lot_size_usd"],
            maker_fee=maker_fee, taker_fee=taker_fee,
        )
        trades = result["trades"].reset_index(drop=True)
        trades["roi"] = trades["pnl"] / sc["lot_size_usd"] if not trades.empty else pd.Series(dtype=float)
        per_stream_trades.append(trades)
        for ti, row in trades.iterrows():
            # exits sort before entries at an identical timestamp -- frees
            # pool capital before deciding the next entry's size, simplest
            # deterministic tie-break and the more conservative direction.
            events.append((row["entry_ts"], 1, "entry", si, ti))
            events.append((row["exit_ts"], 0, "exit", si, ti))
    events.sort(key=lambda e: (e[0], e[1]))

    # Pass 2: walk the merged timeline against one shared pool.
    skim_pct_default = dynamic_skim.get("min_skim_pct", 10.0) if dynamic_skim else 10.0
    target_trades = (dynamic_skim or {}).get("target_trades", 22)
    avg_win_pct   = (dynamic_skim or {}).get("avg_win_pct", 1.8)
    min_skim_pct  = (dynamic_skim or {}).get("min_skim_pct", 10.0)
    max_skim_pct  = (dynamic_skim or {}).get("max_skim_pct", 25.0)

    # starting_pool lets a caller start the pool already above/below baseline
    # (e.g. to stress-test the shrink/floor logic against real bear-market
    # signals without needing a synthetic loss sequence -- see docs/decisions/008
    # Gauntlet results). baseline_total itself (the recovery target and the
    # skim-eligibility threshold) is unaffected either way.
    pool_balance = starting_pool if starting_pool is not None else baseline_total
    entry_capital_used = {}  # (stream_idx, trade_idx) -> $ or None if skipped
    ledger = []
    skims = {}  # ts -> $ skimmed into the bucket this tick

    # Hard floor: the pool level below which EVERY stream's proportional share
    # is under $10, meaning no stream can trade at all -- not a graceful
    # slowdown, a full stop. Below this, nothing can generate the winning
    # trade needed to lift the pool back up on its own (confirmed via the
    # 2026-08-07 Gauntlet stress test, docs/decisions/008 Part 5 -- earlier
    # designs assumed "winnings bring it back above $10" without accounting
    # for every stream pausing simultaneously). This is deliberately treated
    # as a hard stop requiring a real alert + manual capital top-up or a
    # decision to pull the model/stream, NOT an auto-recovery mechanic.
    max_weight = max(sc["lot_size_usd"] for sc in stream_configs) / baseline_total
    hard_floor = 10.0 / max_weight
    halted_at = "start" if pool_balance < hard_floor else None

    for ts, _, kind, si, ti in events:
        sc = stream_configs[si]
        trade = per_stream_trades[si].iloc[ti]

        if kind == "entry":
            weight = sc["lot_size_usd"] / baseline_total
            capital = sc["lot_size_usd"] if pool_balance >= baseline_total else pool_balance * weight
            if capital < 10.0:  # CLAUDE.md's hard minimum lot size, distinct from min_buy_capital below
                entry_capital_used[(si, ti)] = None
                continue
            entry_capital_used[(si, ti)] = capital

        else:  # exit
            capital = entry_capital_used.get((si, ti))
            if capital is None:
                continue  # entry was skipped -- never opened, nothing to close

            pnl_actual = trade["roi"] * capital
            pool_before = pool_balance
            pool_balance += pnl_actual

            if halted_at is None and pool_balance < hard_floor:
                halted_at = ts

            skim_amount = 0.0
            if pnl_actual > 0:
                surplus_before = max(0.0, pool_before - baseline_total)
                surplus_after  = max(0.0, pool_balance - baseline_total)
                new_surplus = surplus_after - surplus_before
                if new_surplus > 0:
                    if dynamic_skim:
                        raw_pct = min_buy_capital / ((avg_win_pct / 100.0) * pool_balance * target_trades) * 100.0
                        skim_pct = max(min_skim_pct, min(max_skim_pct, raw_pct))
                    else:
                        skim_pct = skim_pct_default
                    skim_amount = new_surplus * skim_pct / 100.0
                    pool_balance -= skim_amount
                    skims[ts] = skims.get(ts, 0.0) + skim_amount

            ledger.append({
                "stream_name": sc["stream_name"], "entry_ts": trade["entry_ts"], "exit_ts": ts,
                "capital_used": capital, "pnl": pnl_actual, "skim": skim_amount,
                "pool_after": pool_balance,
            })

    ledger_df = pd.DataFrame(ledger).sort_values("exit_ts").reset_index(drop=True) if ledger else pd.DataFrame()
    skipped = sum(1 for v in entry_capital_used.values() if v is None)

    # Bucket: dip-buy / recover-principal mechanics unchanged from bb18d45,
    # fed by the pooled skim timeline above instead of one stream's trades.
    ref_params = {"primary_timeframe": stream_configs[0]["params"].get("primary_timeframe", "1h"),
                  "filters": {"drawdown_from_high": {"lookback_days": 60}}}
    df = load_market_data(start, end)
    df = add_indicators(df, ref_params)
    if start:
        df = df[df.index >= pd.Timestamp(start)]

    bucket_cash = 0.0
    tracked_qty = 0.0
    tracked_cost_basis = 0.0
    house_money_qty = 0.0
    bucket_events = []

    for ts, row in df.iterrows():
        if ts in skims:
            bucket_cash += skims[ts]

        if tracked_cost_basis > 0:
            current_value = tracked_qty * row["close"]
            if current_value >= tracked_cost_basis * (1 + sell_premium_pct / 100.0):
                qty_to_sell = min(tracked_cost_basis / row["close"] / (1 - taker_fee), tracked_qty)
                cash_recovered = qty_to_sell * row["close"] * (1 - taker_fee)
                house_money_qty += (tracked_qty - qty_to_sell)
                bucket_cash += cash_recovered
                bucket_events.append({"ts": ts, "type": "recover_principal",
                                      "cash_recovered": cash_recovered,
                                      "house_money_added": tracked_qty - qty_to_sell,
                                      "price": row["close"]})
                tracked_qty = 0.0
                tracked_cost_basis = 0.0

        dfh = row.get("drawdown_from_high_pct")
        if bucket_cash >= min_buy_capital and dfh is not None and not pd.isna(dfh) and dfh <= -dip_threshold_pct:
            qty = bucket_cash * (1 - maker_fee) / row["close"]
            tracked_qty += qty
            tracked_cost_basis += bucket_cash
            bucket_events.append({"ts": ts, "type": "buy", "capital": bucket_cash, "price": row["close"]})
            bucket_cash = 0.0

    final_price = df["close"].iloc[-1]
    total_btc_qty = tracked_qty + house_money_qty
    bucket = {
        "events": bucket_events,
        "bucket_cash": bucket_cash,
        "tracked_qty": tracked_qty,
        "tracked_cost_basis": tracked_cost_basis,
        "house_money_qty": house_money_qty,
        "final_price": final_price,
        "final_btc_value": total_btc_qty * final_price,
        "total_holdings_value": bucket_cash + total_btc_qty * final_price,
        "total_skimmed": sum(skims.values()),
    }

    return {
        "baseline_total":     baseline_total,
        "final_pool_balance": pool_balance,
        "bucket":             bucket,
        "combined_ending":    pool_balance + bucket["total_holdings_value"],
        "ledger":             ledger_df,
        "skipped_entries":    skipped,
        "per_stream_trades":  per_stream_trades,
        "hard_floor":         hard_floor,
        "halted_at":          halted_at,  # None = never fully halted; "start" or a real ts = hard stop, needs a real alert
    }
