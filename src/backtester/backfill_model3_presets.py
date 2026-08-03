"""
One-time: run Model 3's model-level backtest across every standard timeframe
preset and save each, matching the preset coverage Models 1/2 already have.

finalize_model3.py only ever ran "Full History" (preset_id=2) -- that's still
the only saved backtest.model_tests row for Model 3, so the Model Dashboard's
"Backtest run" selector has just one option instead of the usual five. This
fills in the rest. Solo-stream model, so every preset's allocation hash is
identical -- next_model_run_number() correctly assigns all of them run_number=1,
same as the existing Full History row.

Usage:
    python -m src.backtester.backfill_model3_presets

Safe to re-run -- skips any preset that already has a saved model_test for
this model.
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.app.db import save_model_test
from src.backtester.model_runner import run_model

load_dotenv()

MODEL_VERSION = 3


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def backfill():
    engine = _get_engine()

    with engine.connect() as conn:
        model_row = conn.execute(
            text("SELECT model_id FROM backtest.models WHERE model_version = :v"),
            {"v": MODEL_VERSION},
        ).fetchone()
        if not model_row:
            raise RuntimeError(f"No backtest.models row for model_version={MODEL_VERSION} -- run finalize_model3.py first.")
        model_id = model_row[0]

        presets = conn.execute(text("""
            SELECT preset_id, name, start_date, end_date
            FROM timeframe_presets WHERE is_active ORDER BY preset_id
        """)).fetchall()

        already_tested = {r[0] for r in conn.execute(
            text("SELECT DISTINCT preset_id FROM backtest.model_tests WHERE model_id = :mid"),
            {"mid": model_id},
        ).fetchall()}

    print(f"Model 3 -> backtest.models.model_id={model_id}")
    print(f"Presets already saved: {sorted(already_tested)}")

    for preset_id, name, start_date, end_date in presets:
        if preset_id in already_tested:
            print(f"Skipping '{name}' (preset_id={preset_id}) -- already saved.")
            continue

        start_str = str(start_date)
        end_str = str(end_date) if end_date else None
        print(f"Running '{name}' ({start_str} -> {end_str or 'latest'})...")

        result = run_model(model_id=model_id, start=start_str, end=end_str)
        print(f"  annualized_return={result['annualized_return']}  trades={result['combined_trades']}  "
              f"win_rate={result['win_rate']}  max_drawdown={result['max_drawdown']}")

        import pickle
        from pathlib import Path
        payload_path = Path(__file__).parent.parent / "app" / ".last_model_run.pkl"
        with open(payload_path, "rb") as f:
            payload = pickle.load(f)

        model_test_id, run_num = save_model_test(
            payload,
            preset_id=preset_id,
            notes="Model 3 preset backfill -- solo-stream model, same locked config as the Full History run.",
        )
        print(f"  Saved model_test_id={model_test_id} (run #{run_num})")

    print("\nDone.")


if __name__ == "__main__":
    backfill()
