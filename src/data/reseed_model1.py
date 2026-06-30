"""
Reseed Model 1 after the v2 schema reset.
- Creates backtest.models row for Model 1
- Runs MR v1, DH v1, BS v1 against all 4 timeframe presets
- Saves all 12 stream_tests rows
- Locks streams into backtest.streams with locked_test_id = Primary Window test
Run: source .venv/bin/activate && python -m src.data.reseed_model1
"""
import json
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import pandas as pd

from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold

load_dotenv()

RUNS_DIR = Path(__file__).parent.parent / "app" / "runs"
RUNS_DIR.mkdir(exist_ok=True)

LOT_SIZE   = 10.0
SLOT_COUNT = 1
SLOT_MODE  = "single"


def get_engine():
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


MR_PARAMS = {
    "primary_timeframe": "1h",
    "core_signal": "ema_crossover",
    "core_params": {"ema_short": 20, "ema_long": 50},
    "filters": {
        "trend_context": {"sma_period": 200, "require": "above"},
        "rsi": {"period": 14, "min": 55, "max": None},
    },
    "sentiment": {"fear_greed": {"min": 25, "max": None}},
    "position": {
        "trailing_stop_pct": 5.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

DH_PARAMS = {
    "primary_timeframe": "1h",
    "core_signal": "rsi_recovery",
    "core_params": {
        "rsi_period": 14,
        "rsi_threshold": 30,
        "require_bullish_candle": True,
    },
    "filters": {
        "drawdown_from_high": {"lookback_days": 90, "min_drop_pct": 25.0},
    },
    "position": {
        "trailing_stop_pct": 7.5,
        "entry_order_type": "limit",
        "entry_expiry_candles": 1,
        "min_hold_candles": 48,
    },
    "sentiment": {"fear_greed": {"max": 20}},
}

BS_PARAMS = {
    "primary_timeframe": "1h",
    "core_signal": "range_breakout",
    "core_params": {"breakout_lookback": 48},
    "filters": {
        "atr_regime": {"period": 14, "avg_period": 30, "max_pct_of_avg": 90},
        "bollinger": {
            "period": 20, "std_dev": 2.0,
            "squeeze": {"max_bandwidth_pct": 6.0},
        },
        "breakout_candle": {"body_ratio_min": 0.4, "close_position_min": 0.6},
    },
    "sentiment": {"fear_greed": {"min": 50}},
    "position": {
        "trailing_stop_pct": 5.0,
        "entry_order_type": "limit",
        "entry_expiry_candles": 2,
    },
}

STREAMS = [
    {
        "name": "Momentum Rider", "version": "v1",
        "strategy_type": "trend",
        "params": MR_PARAMS, "grade": 4,
        "description": (
            "Trend-following. Enters when 20 EMA crosses above 50 EMA on 1h candles, "
            "price above 200 SMA, RSI > 55, F&G > 25. 5% trailing stop."
        ),
    },
    {
        "name": "Dip Hunter", "version": "v1",
        "strategy_type": "mean_reversion",
        "params": DH_PARAMS, "grade": 4,
        "description": (
            "Fear bounce. RSI(14) recovery through 30 (bullish candle required), "
            "price 25%+ below 90-day high, F&G < 20. 7.5% trailing stop, 48-candle min hold."
        ),
    },
    {
        "name": "Breakout Scout", "version": "v1",
        "strategy_type": "breakout",
        "params": BS_PARAMS, "grade": 4,
        "description": (
            "Consolidation breakout. 48-candle range breakout with ATR low-vol "
            "filter, Bollinger squeeze (6%), candle quality gate, and F&G > 50. "
            "5% trailing stop."
        ),
    },
]


def save_test(conn, stream_name, version, run_number, preset_id, result, metrics,
              initial_capital, ending_balance, payload):
    row = conn.execute(text("""
        INSERT INTO backtest.stream_tests (
            stream_name, stream_version, run_number,
            preset_id, custom_start, custom_end,
            simulation_start, simulation_end,
            slot_count, slot_mode,
            parameters, initial_capital, ending_balance,
            total_trades, win_rate, total_pnl, total_return_pct,
            annualized_return_pct, avg_winner_pct, avg_loser_pct,
            profit_factor, max_drawdown_pct, avg_hold_candles
        ) VALUES (
            :stream_name, :version, :run_number,
            :preset_id, NULL, NULL,
            :simulation_start, :simulation_end,
            :slot_count, :slot_mode,
            :parameters, :initial_capital, :ending_balance,
            :total_trades, :win_rate, :total_pnl, :total_return_pct,
            :annualized_return_pct, :avg_winner_pct, :avg_loser_pct,
            :profit_factor, :max_drawdown_pct, :avg_hold_candles
        ) RETURNING test_id
    """), {
        "stream_name":           stream_name,
        "version":               version,
        "run_number":            run_number,
        "preset_id":             preset_id,
        "simulation_start":      result["start"],
        "simulation_end":        result["end"],
        "slot_count":            SLOT_COUNT,
        "slot_mode":             SLOT_MODE,
        "parameters":            json.dumps(payload["params"]),
        "initial_capital":       initial_capital,
        "ending_balance":        ending_balance,
        "total_trades":          metrics["total_trades"],
        "win_rate":              metrics["win_rate"],
        "total_pnl":             metrics["total_pnl"],
        "total_return_pct":      metrics["total_return_pct"],
        "annualized_return_pct": metrics["annualized_return_pct"],
        "avg_winner_pct":        metrics["avg_winner_pct"],
        "avg_loser_pct":         metrics["avg_loser_pct"],
        "profit_factor":         metrics["profit_factor"],
        "max_drawdown_pct":      metrics["max_drawdown_pct"],
        "avg_hold_candles":      metrics["avg_hold_candles"],
    })
    test_id = row.scalar()

    pkl_path = RUNS_DIR / f"{test_id}.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(payload, f)

    return test_id


def main():
    engine = get_engine()

    # Load presets
    with engine.connect() as conn:
        presets = pd.read_sql(text("""
            SELECT preset_id, name, start_date, end_date
            FROM timeframe_presets WHERE is_active = TRUE
            ORDER BY start_date
        """), conn).to_dict("records")

    primary_preset_id = next(p["preset_id"] for p in presets if p["name"] == "Primary Window")
    print(f"Presets loaded: {[p['name'] for p in presets]}")

    # Create Model 1
    with engine.connect() as conn:
        row = conn.execute(text("""
            INSERT INTO backtest.models (model_version, description)
            VALUES (1, 'Three-stream model: trend (MR), mean reversion (DH), breakout (BS). Equal allocation $16.67/lot × 2 slots × 3 streams.')
            RETURNING model_id
        """))
        model_id = row.scalar()
        conn.commit()
    print(f"\nModel 1 created: model_id={model_id}")

    # Run each stream × each preset
    for stream_cfg in STREAMS:
        full_name = f"{stream_cfg['name']} {stream_cfg['version']}"
        print(f"\n{'='*50}")
        print(f"{full_name}")
        print('='*50)

        primary_test_id = None

        with engine.connect() as conn:
            for i, preset in enumerate(presets, start=1):
                start = str(preset["start_date"])
                end   = str(preset["end_date"]) if preset["end_date"] else None

                result = run_backtest(
                    stream_cfg["params"],
                    start=start, end=end,
                    slot_count=SLOT_COUNT, slot_mode=SLOT_MODE,
                    stream_name=full_name, lot_size_usd=LOT_SIZE,
                )

                trades          = result["trades"]
                initial_capital = LOT_SIZE * SLOT_COUNT
                metrics         = compute_metrics(trades, initial_capital, result["start"], result["end"])
                ending_balance  = initial_capital + (trades["pnl"].sum() if not trades.empty else 0)
                bh              = btc_buy_and_hold(result["df"], initial_capital)

                payload = {
                    "stream_name":     full_name,
                    "params":          stream_cfg["params"],
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

                test_id = save_test(
                    conn,
                    stream_name=stream_cfg["name"],
                    version=stream_cfg["version"],
                    run_number=1,
                    preset_id=preset["preset_id"],
                    result=result,
                    metrics=metrics,
                    initial_capital=initial_capital,
                    ending_balance=ending_balance,
                    payload=payload,
                )

                if preset["preset_id"] == primary_preset_id:
                    primary_test_id = test_id

                ann = metrics["annualized_return_pct"]
                ann_str = f"{ann:+.1f}%" if ann is not None else "—"
                print(f"  [{preset['name']}] test_id={test_id} | {metrics['total_trades']} trades | {ann_str} ann | ${ending_balance:.2f}")

            conn.commit()

        # Lock stream into model
        with engine.connect() as conn:
            row = conn.execute(text("""
                INSERT INTO backtest.streams (
                    model_id, stream_name, stream_version, strategy_type,
                    parameters, slot_count, slot_mode, lot_size_usd,
                    locked_test_id, grade, description
                ) VALUES (
                    :model_id, :stream_name, :stream_version, :strategy_type,
                    :parameters, :slot_count, :slot_mode, :lot_size_usd,
                    :locked_test_id, :grade, :description
                ) RETURNING stream_id
            """), {
                "model_id":       model_id,
                "stream_name":    stream_cfg["name"],
                "stream_version": stream_cfg["version"],
                "strategy_type":  stream_cfg["strategy_type"],
                "parameters":     json.dumps(stream_cfg["params"]),
                "slot_count":     SLOT_COUNT,
                "slot_mode":      SLOT_MODE,
                "lot_size_usd":   LOT_SIZE,
                "locked_test_id": primary_test_id,
                "grade":          stream_cfg["grade"],
                "description":    stream_cfg["description"],
            })
            stream_id = row.scalar()
            conn.commit()
        print(f"  → Locked as stream_id={stream_id}, locked_test_id={primary_test_id}")

    print("\n✓ All done. Model 1 reseeded with 12 stream tests (3 streams × 4 presets).")


if __name__ == "__main__":
    main()
