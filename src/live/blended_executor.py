"""
Live trading executor for Model 3 (Grid Stacker Blended) -- single-invocation
tick for GitHub Actions, structurally parallel to executor.py (Model 1) but
fully independent: separate DB tables (live.blended_positions/_fills/_capital),
separate live.executor_state row (keyed by model_id instead of the shared
singleton), separate GitHub Actions workflow + cron-job.org schedule, and
(once cut) a separate live-model-3 branch. Never writes to live.lots and never
reads Kraken's account balance for position sizing -- see
blended_order_manager.py's capital-ledger design for why.

On each run:
  1. Read last_run_at from live.executor_state (model_id=3's row)
  2. Detect which candle timeframes closed since last run
  3. For the stream's timeframe closing: check cascade-add trigger on any
     OPEN position, then (if no position building/open) check for a fresh
     entry signal
  4. Poll pending slot-1 entries and pending cascade adds for fills/expiry
  5. Check trailing stop / capitulation stop on the OPEN position
  6. Write last_run_at back to DB

Usage:
    python -m src.live.blended_executor              # live mode — real Kraken orders
    python -m src.live.blended_executor --dry-run    # no real orders; DB writes are real
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.live import blended_order_manager as order_manager
from src.live import blended_position_monitor as position_monitor
from src.live import signal_engine
from src.live.executor import (
    _detect_closed_timeframes,
    _ensure_market_data_fresh,
    _latest_candle_for_stream,
)
from src.live.kraken_client import KrakenClient
from src.live.notifier import alert_system_down

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LIVE_MODEL_VERSION = 3


def _get_engine():
    url = os.getenv("SUPABASE_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "SUPABASE_DATABASE_URL is not set. "
            "This must point to Supabase. Do not use DATABASE_URL for the executor."
        )
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    # See executor.py's _get_engine -- a stalled connection or locked/slow query
    # otherwise blocks with zero output until the CI job's own timeout kills it.
    return create_engine(url, connect_args={"connect_timeout": 10})


def _load_streams(conn) -> dict:
    rows = conn.execute(
        text("""
            SELECT ls.stream_id, ls.model_id, ls.stream_name, ls.stream_version,
                   ls.strategy_type, ls.parameters, ls.slot_count, ls.slot_mode, ls.lot_size_usd
            FROM live.streams ls
            JOIN live.models lm ON ls.model_id = lm.model_id
            WHERE lm.model_version = :ver AND lm.status = 'active'
        """),
        {"ver": LIVE_MODEL_VERSION},
    ).fetchall()
    return {r.stream_id: dict(r._mapping) for r in rows}


def _read_last_run(conn, model_id: int) -> datetime:
    row = conn.execute(
        text("SELECT last_run_at FROM live.executor_state WHERE model_id = :mid"),
        {"mid": model_id},
    ).fetchone()
    if row is None:
        return datetime.now(timezone.utc)
    ts = row.last_run_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _write_last_run(conn, model_id: int, now: datetime) -> None:
    result = conn.execute(
        text("UPDATE live.executor_state SET last_run_at = :now WHERE model_id = :mid"),
        {"now": now, "mid": model_id},
    )
    if result.rowcount == 0:
        # id has DEFAULT 1 (a leftover from the old singleton design) -- must be
        # given explicitly here or this collides with Model 1's id=1 row.
        conn.execute(
            text("INSERT INTO live.executor_state (id, last_run_at, model_id) VALUES (:mid, :now, :mid)"),
            {"now": now, "mid": model_id},
        )


def _log_tick(conn, model_id: int, last_tick: datetime, closed_tfs: set, open_count: int,
              pending_count: int, signals_fired: list, entries_placed: int,
              fills: int, expirations: int, stops_triggered: int,
              error: str = None) -> None:
    conn.execute(text(
        "DELETE FROM live.executor_runs WHERE ran_at < now() - interval '90 days' AND model_id = :mid"
    ), {"mid": model_id})
    conn.execute(text("""
        INSERT INTO live.executor_runs
            (last_tick_at, closed_tfs, open_lots, pending_lots, signals_fired,
             entries_placed, fills, expirations, stops_triggered, error, model_id)
        VALUES
            (:last_tick, :closed_tfs, :open, :pending, :signals,
             :entries, :fills, :expirations, :stops, :error, :mid)
    """), {
        "last_tick":   last_tick,
        "closed_tfs":  list(closed_tfs) if closed_tfs else [],
        "open":        open_count,
        "pending":     pending_count,
        "signals":     signals_fired,
        "entries":     entries_placed,
        "fills":       fills,
        "expirations": expirations,
        "stops":       stops_triggered,
        "error":       error,
        "mid":         model_id,
    })


def tick(conn, streams: dict, kraken: KrakenClient, last_tick: datetime,
         now: datetime, dry_run: bool) -> None:
    closed_tfs = _detect_closed_timeframes(last_tick, now)
    _ensure_market_data_fresh(conn, now, closed_tfs, LIVE_MODEL_VERSION)
    open_count = conn.execute(text("SELECT COUNT(*) FROM live.blended_positions WHERE status = 'OPEN'")).scalar()
    pending_count = conn.execute(text("SELECT COUNT(*) FROM live.blended_positions WHERE status = 'PENDING_ENTRY'")).scalar()
    log.info(
        f"Tick — last_run={last_tick.strftime('%Y-%m-%d %H:%M')} "
        f"closed_tfs={closed_tfs or 'none'} "
        f"open={open_count} pending={pending_count}"
        f"{' [DRY RUN]' if dry_run else ''}"
    )

    signals_fired = []
    entries_placed = 0

    # One pass per stream whose timeframe closed this tick: fetch its candle,
    # check for a cascade add, then check for a fresh entry signal. Each
    # stream's decisions only depend on its own candle, so there's no
    # ordering requirement forcing this into two separate passes -- candle_row
    # just needs to be fully populated by the time position_monitor.check_all
    # runs below, which it is either way.
    candle_row = {}
    for stream_id, stream in streams.items():
        tf = stream["parameters"].get("primary_timeframe", "4h")
        if tf not in closed_tfs:
            continue

        candle = _latest_candle_for_stream(stream)
        if candle:
            candle_row[stream_id] = candle

        # An OPEN position with room to add takes priority over trying to
        # open a brand-new one (has_active_position blocks that anyway),
        # but check it explicitly so the add uses this tick's fresh close.
        if candle:
            order_manager.check_cascade_add_trigger(conn, stream, candle["close"], kraken, dry_run)

        if order_manager.has_active_position(conn, stream_id):
            log.debug(f"{stream['stream_name']}: position already building/open, skipping signal check")
            continue

        try:
            fired = signal_engine.check(stream)
        except Exception as e:
            log.error(f"Signal check failed for {stream['stream_name']}: {e}")
            continue
        if fired:
            log.info(f"Signal fired: {stream['stream_name']} — placing slot-1 entry order")
            signals_fired.append(stream["stream_name"])
            order_manager.place_entry(conn, stream, kraken, dry_run)
            entries_placed += 1
        else:
            log.debug(f"{stream['stream_name']}: no signal")

    entry_fills, entry_expirations = order_manager.check_pending_entry(conn, kraken, streams, dry_run)
    add_fills, add_expirations = order_manager.check_pending_add(conn, kraken, streams, dry_run)
    fills = entry_fills + add_fills
    expirations = entry_expirations + add_expirations

    stops_triggered = 0
    if closed_tfs and candle_row:
        stops_triggered = position_monitor.check_all(
            conn, streams, candle_row, closed_tfs, kraken, dry_run
        )

    model_id = next(iter(streams.values()))["model_id"] if streams else None
    _log_tick(conn, model_id, last_tick, closed_tfs, open_count, pending_count,
              signals_fired, entries_placed, fills, expirations, stops_triggered)


def run(dry_run: bool = False) -> None:
    mode = "DRY RUN" if dry_run else "LIVE"
    log.info(f"=== Forge Anchor Executor — Model {LIVE_MODEL_VERSION} (Grid Stacker Blended) [{mode}] ===")

    engine = _get_engine()
    kraken = KrakenClient()

    if not dry_run:
        try:
            balance = kraken.validate_connection()
            usd = float(balance.get("ZUSD", 0))
            btc = float(balance.get("XXBT", 0))
            log.info(f"Kraken connected — USD: ${usd:.2f}  BTC: {btc:.8f}")
        except Exception as e:
            log.error(f"Kraken connection failed: {e}")
            sys.exit(1)

    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(text("SET statement_timeout = '15s'"))
        streams = _load_streams(conn)
        if not streams:
            log.error(f"No active streams found for Model {LIVE_MODEL_VERSION}. Run deploy_model3.py first.")
            sys.exit(1)
        log.info(f"Loaded {len(streams)} stream(s): {[s['stream_name'] for s in streams.values()]}")

        model_id = next(iter(streams.values()))["model_id"]
        last_tick = _read_last_run(conn, model_id)

        gap_hours = (now - last_tick).total_seconds() / 3600
        if gap_hours > 2:
            log.warning(f"Executor gap detected: {gap_hours:.1f}h since last tick — firing system-down alert")
            alert_system_down(gap_hours)

        tick(conn, streams, kraken, last_tick, now, dry_run)
        _write_last_run(conn, model_id, now)

    log.info("=== Tick complete ===")


def main():
    parser = argparse.ArgumentParser(description="Forge Anchor Model 3 (blended) live executor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without placing real Kraken orders (DB writes are real)")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
