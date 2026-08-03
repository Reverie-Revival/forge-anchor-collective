"""
One-time: re-run Model 1, Model 2, and Model 3's model-level backtests across
all 5 timeframe presets, now that MAKER_FEE/TAKER_FEE reflect Kraken's real
lowest-volume-tier rates (0.40%/0.80%, confirmed via TradeVolume API
2026-08-03) instead of the previously-assumed 0.25%/0.40%.

Also corrects two stale backtest.model_streams rows discovered along the way
(fixed directly in the DB before this script runs, not here):
  - Model 1: lot_size_usd was stuck at the $10 default, not the real deployed
    $33.33/stream.
  - Model 2: Momentum Rider was linked to the wrong stream_config (v3
    staggered $12.50) instead of Run 3's actual selected config (v4 single
    $25).

Usage:
    python -m src.backtester.rerun_corrected_fees
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.app.db import save_model_test
from src.backtester.model_runner import run_model

load_dotenv()

NOTE = (
    "Re-run 2026-08-03 with corrected fees (MAKER 0.40%/TAKER 0.80%, Kraken's "
    "real lowest-volume tier -- was wrongly assumed 0.25%/0.40%). See HANDOFF.md."
)


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def run_all_presets(model_id: int, label: str):
    engine = _get_engine()
    with engine.connect() as conn:
        presets = conn.execute(text("""
            SELECT preset_id, name, start_date, end_date
            FROM timeframe_presets WHERE is_active ORDER BY preset_id
        """)).fetchall()

    print(f"\n=== {label} (backtest.models.model_id={model_id}) ===")
    for preset_id, name, start_date, end_date in presets:
        start_str = str(start_date)
        end_str = str(end_date) if end_date else None
        result = run_model(model_id=model_id, start=start_str, end=end_str)
        print(f"  {name:15s}  ann={result['annualized_return']}  "
              f"trades={result['combined_trades']}  win_rate={result['win_rate']}  "
              f"max_dd={result['max_drawdown']}")

        import pickle
        from pathlib import Path
        payload_path = Path(__file__).parent.parent / "app" / ".last_model_run.pkl"
        with open(payload_path, "rb") as f:
            payload = pickle.load(f)

        model_test_id, run_num = save_model_test(payload, preset_id=preset_id, notes=NOTE)
        print(f"    -> saved model_test_id={model_test_id} (run #{run_num})")


if __name__ == "__main__":
    run_all_presets(1, "Model 1 (live, $33.33/stream)")
    run_all_presets(2, "Model 2 (Run 3 config, corrected)")
    run_all_presets(4, "Model 3 (Grid Stacker Blended)")
    print("\nDone.")
