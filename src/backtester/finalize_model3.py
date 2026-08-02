"""
One-time: finalize Model 3 (Grid Stacker Blended) per the project's normal
model lifecycle -- assemble the locked stream config into backtest.model_streams,
run a model-level backtest, and save it to backtest.model_tests. Solo-stream
model, so the model-level result is mathematically identical to the stream-level
result already validated exhaustively (see HANDOFF.md) -- there's no cross-stream
interaction to test -- but this keeps Model 3 gated the same way Models 1/2 are,
with a real model_tests row for live.models.based_on_model_test_id to reference.

Usage:
    python -m src.backtester.finalize_model3

Safe to inspect -- will abort if Model 3 already has a backtest.models row.
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.app.db import save_model_test
from src.backtester.model_runner import run_model

load_dotenv()

MODEL_VERSION = 3
STREAM_CONFIG_ID = 36   # Grid Stacker Blended v8 -- the locked, validated config
LOT_SIZE_USD = 100.00   # blended mode: this IS total capital, not per-slot
PRESET_ID = 2           # "Full History" -- matches HANDOFF's headline validated number
DESCRIPTION = "Model 3 — Grid Stacker Blended v8 — solo stream, $100, compounding"


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def finalize():
    engine = _get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT model_id FROM backtest.models WHERE model_version = :v"),
            {"v": MODEL_VERSION},
        ).fetchone()
        if existing:
            print(f"backtest.models already has Model {MODEL_VERSION} (model_id={existing[0]}). Nothing to do.")
            return existing[0]

        row = conn.execute(
            text("""
                INSERT INTO backtest.models (model_version, description, status)
                VALUES (:ver, :desc, 'active')
                RETURNING model_id
            """),
            {"ver": MODEL_VERSION, "desc": DESCRIPTION},
        )
        model_id = row.scalar()
        print(f"Created backtest.models row: model_id={model_id}")

        conn.execute(
            text("""
                INSERT INTO backtest.model_streams (model_id, stream_config_id, lot_size_usd)
                VALUES (:mid, :cid, :lot)
            """),
            {"mid": model_id, "cid": STREAM_CONFIG_ID, "lot": LOT_SIZE_USD},
        )
        print(f"Assembled model_streams: stream_config_id={STREAM_CONFIG_ID} @ ${LOT_SIZE_USD:.2f}")

    with engine.connect() as conn:
        preset = conn.execute(
            text("SELECT start_date, end_date FROM timeframe_presets WHERE preset_id = :pid"),
            {"pid": PRESET_ID},
        ).fetchone()
    preset_start = str(preset.start_date)
    preset_end = str(preset.end_date) if preset.end_date else None

    print(f"Running model-level backtest ({preset_start} -> {preset_end or 'latest'})...")
    result = run_model(model_id=model_id, start=preset_start, end=preset_end)
    print(f"  annualized_return={result['annualized_return']}  trades={result['combined_trades']}  "
          f"win_rate={result['win_rate']}  max_drawdown={result['max_drawdown']}")

    import pickle
    from pathlib import Path
    payload_path = Path(__file__).parent.parent / "app" / ".last_model_run.pkl"
    with open(payload_path, "rb") as f:
        payload = pickle.load(f)

    model_test_id, run_num = save_model_test(
        payload,
        preset_id=PRESET_ID,
        notes="Model 3 finalization -- solo-stream model-level backtest, matches stream-level validation (see HANDOFF.md).",
    )
    print(f"Saved backtest.model_tests row: model_test_id={model_test_id} (run #{run_num})")
    print(f"\nUse model_test_id={model_test_id} as BASED_ON_MODEL_TEST_ID in deploy_model3.py")
    return model_test_id


if __name__ == "__main__":
    finalize()
