"""
Quick runner for Stream Tester experiments.
Writes result to .last_run.pkl so it shows up in the Streamlit app immediately.

Usage:
    python -m src.backtester.run_stream_test
Edit the PARAMS and CONFIG block below before each run.
"""
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold
from src.app.db import get_engine, LAST_RUN_PATH

# ── Configure this block for each test ────────────────────────────────────────

STREAM_NAME = "Dip Hunter v2"
LOT_SIZE    = 10.0
SLOT_COUNT  = 1
SLOT_MODE   = "single"
PRESET_NAME = "Full History"   # must match timeframe_presets.name

PARAMS = {
    "primary_timeframe": "1h",
    "core_signal": "rsi_recovery",
    "core_params": {
        "rsi_period": 14,
        "rsi_threshold": 30,
        "require_bullish_candle": True,
    },
    "filters": {
        "drawdown_from_high": {
            "min_drop_pct": 25.0,
            "lookback_days": 90,
        },
        "rsi": {"min": 35},
    },
    "sentiment": {"fear_greed": {"max": 20}},
    "position": {
        "trailing_stop_pct": 10.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 1,
        "min_hold_candles": 48,
        "max_hold_candles": 240,
    },
}

# ──────────────────────────────────────────────────────────────────────────────

def main():
    engine = get_engine()

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT start_date, end_date FROM timeframe_presets WHERE name = :n AND is_active = TRUE"
        ), {"n": PRESET_NAME}).fetchone()

    if not row:
        print(f"ERROR: preset '{PRESET_NAME}' not found")
        sys.exit(1)

    start = str(row[0])
    end   = str(row[1]) if row[1] else None
    print(f"Running {STREAM_NAME} | {PRESET_NAME} | {start} → {end or 'now'}")
    print(f"  trail={PARAMS['position']['trailing_stop_pct']}% | slot_mode={SLOT_MODE} | lot=${LOT_SIZE}")

    result = run_backtest(
        PARAMS,
        start=start, end=end,
        slot_count=SLOT_COUNT, slot_mode=SLOT_MODE,
        stream_name=STREAM_NAME, lot_size_usd=LOT_SIZE,
    )

    trades          = result["trades"]
    initial_capital = LOT_SIZE * SLOT_COUNT
    metrics         = compute_metrics(trades, initial_capital, result["start"], result["end"])
    ending_balance  = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)
    bh              = btc_buy_and_hold(result["df"], initial_capital)

    payload = {
        "stream_name":     STREAM_NAME,
        "params":          PARAMS,
        "result":          result,
        "trades":          trades,
        "df":              result["df"],
        "metrics":         metrics,
        "bh":              bh,
        "initial_capital": initial_capital,
        "ending_balance":  ending_balance,
        "slot_count":      SLOT_COUNT,
        "slot_mode":       SLOT_MODE,
        "lot_size_usd":    LOT_SIZE,
    }

    with open(LAST_RUN_PATH, "wb") as f:
        pickle.dump(payload, f)

    ann  = metrics.get("annualized_return_pct", 0)
    tot  = metrics.get("total_trades", 0)
    wr   = metrics.get("win_rate", 0)
    dd   = metrics.get("max_drawdown_pct", 0)
    pf   = metrics.get("profit_factor", 0)

    print(f"\n  Ann. return : {ann:+.1f}%")
    print(f"  Trades      : {tot}")
    print(f"  Win rate    : {wr:.0%}")
    print(f"  Max DD      : {dd:.1f}%")
    print(f"  Profit factor: {pf:.2f}")
    print(f"\n  → Saved to .last_run.pkl — open Stream Tester to review")


if __name__ == "__main__":
    main()
