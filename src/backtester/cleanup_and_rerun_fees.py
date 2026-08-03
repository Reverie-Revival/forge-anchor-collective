"""
One-time: clean up and re-run backtest data for the currently-locked/deployed
stream configs and assembled model compositions (Model 1 live, Model 2 Run 3,
Model 3 live), now that:
  1. Fee round-trip math is correct (maker entry, taker exit -- see
     src/backtester/engine.py, fixed 2026-08-03).
  2. Fees are threaded as explicit parameters (src/fees.py is the single
     source of truth for the default).
  3. save_model_test() upserts in place instead of always inserting.

The DB had accumulated real duplicate rows for these configs/models --
re-runs across multiple fee-correction passes each left a new row instead of
replacing the old one (save_model_test had no dedup at all; some stream_tests
rows predate save_stream_test's dedup logic). This script deletes every
preset-based row for the bounded set below, then re-runs and saves fresh --
guaranteeing exactly one row per preset afterward, tagged with the fee rate
that produced it.

Deliberately NOT touched: any other stream_configs (exploratory dead-ends --
VR v2/v3/v4, Quiet Climber, Cascade DCA, SMA Pullback, etc.), any other
model run_numbers (Model 2's Run 1/2/4, not selected), and any custom-date
rows (the 56 regime-robustness tests) -- all legacy, left alone per explicit
instruction. Legacy rows have NULL fee_maker_pct/fee_taker_pct and are
flagged as such in the UI.

Usage:
    python -m src.backtester.cleanup_and_rerun_fees
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.app.db import save_stream_test, save_model_test
from src.backtester.engine import run_backtest
from src.backtester.metrics import compute_metrics, btc_buy_and_hold
from src.backtester.model_runner import run_model, _load_locked_streams

load_dotenv()

STREAM_INITIAL_CAPITAL = 20.0  # matches src/app/stream_tester.py's convention

# (model_id, run_number) for the currently-locked/selected composition of each model.
# Confirmed against backtest.model_streams before running this.
BOUNDED_MODELS = [
    (1, 4, "Model 1 (live)"),
    (2, 3, "Model 2 (Run 3 selected)"),
    (4, 1, "Model 3 (Grid Stacker, live)"),
]


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _bounded_stream_config_ids() -> list:
    """Every stream_config_id currently used by any bounded model's composition."""
    engine = _get_engine()
    model_ids = [m[0] for m in BOUNDED_MODELS]
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT stream_config_id FROM backtest.model_streams
            WHERE model_id = ANY(:mids)
        """), {"mids": model_ids}).fetchall()
    return [r[0] for r in rows]


def _presets():
    engine = _get_engine()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT preset_id, name, start_date, end_date FROM timeframe_presets
            WHERE is_active ORDER BY preset_id
        """)).fetchall()


def cleanup():
    engine = _get_engine()
    cfg_ids = _bounded_stream_config_ids()
    print(f"Bounded stream_config_ids: {cfg_ids}")

    with engine.connect() as conn:
        deleted = conn.execute(text("""
            DELETE FROM backtest.stream_tests
            WHERE stream_config_id = ANY(:ids) AND preset_id IS NOT NULL
        """), {"ids": cfg_ids})
        print(f"Deleted {deleted.rowcount} stream_tests rows")

        for model_id, run_number, label in BOUNDED_MODELS:
            mtids = conn.execute(text("""
                SELECT model_test_id FROM backtest.model_tests
                WHERE model_id = :mid AND run_number = :rn AND preset_id IS NOT NULL
            """), {"mid": model_id, "rn": run_number}).fetchall()
            mtids = [r[0] for r in mtids]
            if mtids:
                conn.execute(text("DELETE FROM backtest.lots WHERE model_test_id = ANY(:ids)"),
                            {"ids": mtids})
                conn.execute(text("DELETE FROM backtest.model_tests WHERE model_test_id = ANY(:ids)"),
                            {"ids": mtids})
            print(f"Deleted {len(mtids)} model_tests rows for {label}")

        conn.commit()
    return cfg_ids


def rerun_streams(cfg_ids: list):
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT sc.stream_config_id, sc.version, sc.parameters, sc.slot_count, sc.slot_mode,
                   s.stream_name
            FROM backtest.stream_configs sc
            JOIN backtest.streams s ON sc.stream_id = s.stream_id
            WHERE sc.stream_config_id = ANY(:ids)
        """), {"ids": cfg_ids}).fetchall()
        # blended mode's lot_size_usd IS the total capital pool (matches
        # model_engine.py's own special-case) -- the generic $20-per-lot
        # stream-tester convention is wrong for it; use the real deployed
        # total from model_streams instead.
        blended_capital = dict(engine.connect().execute(text("""
            SELECT sc.stream_config_id, ms.lot_size_usd
            FROM backtest.model_streams ms
            JOIN backtest.stream_configs sc ON ms.stream_config_id = sc.stream_config_id
            WHERE sc.stream_config_id = ANY(:ids) AND sc.slot_mode = 'blended'
        """), {"ids": cfg_ids}).fetchall())

    presets = _presets()
    for stream_config_id, version, params, slot_count, slot_mode, stream_name in rows:
        full_name = f"{stream_name} {version}"
        initial_capital = float(blended_capital[stream_config_id]) if stream_config_id in blended_capital \
            else STREAM_INITIAL_CAPITAL
        print(f"\n=== {full_name} (stream_config_id={stream_config_id}, capital=${initial_capital:.2f}) ===")
        for preset_id, preset_name, start_date, end_date in presets:
            result = run_backtest(
                params=params, start=str(start_date), end=str(end_date) if end_date else None,
                slot_count=slot_count, slot_mode=slot_mode, stream_name=full_name,
                lot_size_usd=initial_capital,
            )
            metrics = compute_metrics(result["trades"], initial_capital, result["start"], result["end"])
            ending  = initial_capital + (metrics["total_pnl"] or 0)
            payload = {
                "stream_name": full_name, "stream_config_id": stream_config_id,
                "params": params, "result": result, "trades": result["trades"], "df": result["df"],
                "metrics": metrics, "initial_capital": initial_capital, "ending_balance": ending,
                "slot_count": slot_count, "slot_mode": slot_mode, "lot_size_usd": initial_capital,
            }
            save_stream_test(
                stream_config_id=stream_config_id, params=params, result=result, metrics=metrics,
                initial_capital=initial_capital, ending_balance=ending, payload=payload,
                preset_id=preset_id,
                notes="Re-run 2026-08-03 with corrected fee math (maker-entry/taker-exit) and "
                      "fee-column tracking. See HANDOFF.md.",
            )
            ann = metrics["annualized_return_pct"]
            print(f"  {preset_name:15s}  ann={ann:+.1f}%  trades={metrics['total_trades']}"
                  if ann is not None else f"  {preset_name:15s}  ann=—  trades={metrics['total_trades']}")


def rerun_models():
    presets = _presets()
    for model_id, run_number, label in BOUNDED_MODELS:
        print(f"\n=== {label} (model_id={model_id}) ===")
        for preset_id, preset_name, start_date, end_date in presets:
            result = run_model(model_id=model_id, start=str(start_date),
                               end=str(end_date) if end_date else None)

            import pickle
            from pathlib import Path
            payload_path = Path(__file__).parent.parent / "app" / ".last_model_run.pkl"
            with open(payload_path, "rb") as f:
                payload = pickle.load(f)

            model_test_id, saved_run_num = save_model_test(
                payload, preset_id=preset_id,
                notes="Re-run 2026-08-03 with corrected fee math (maker-entry/taker-exit) and "
                      "fee-column tracking. See HANDOFF.md.",
            )
            assert saved_run_num == run_number, (
                f"run_number mismatch for {label}/{preset_name}: expected {run_number}, "
                f"got {saved_run_num} -- composition may have drifted from what BOUNDED_MODELS assumes"
            )
            print(f"  {preset_name:15s}  ann={result['annualized_return']}  "
                  f"trades={result['combined_trades']}  model_test_id={model_test_id}")


if __name__ == "__main__":
    cfg_ids = cleanup()
    rerun_streams(cfg_ids)
    rerun_models()
    print("\nDone.")
