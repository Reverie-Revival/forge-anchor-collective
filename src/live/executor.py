"""
Live trading executor — single-invocation tick for GitHub Actions.

Invoked every 30 minutes by a GitHub Actions cron workflow. On each run:
  1. Read last_run_at from live.executor_state
  2. Detect which candle timeframes (1h, 4h) closed since last run
  3. For streams whose timeframe closed: check for new signals → place entry orders
  4. Poll all PENDING lots for fills / expiry
  5. Check trailing stops on all OPEN lots
  6. Write last_run_at back to DB

Usage:
    python -m src.live.executor              # live mode — real Kraken orders
    python -m src.live.executor --dry-run    # no real orders; DB writes are real
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.live import order_manager, position_monitor, signal_engine
from src.live.kraken_client import KrakenClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

LIVE_MODEL_VERSION = 1


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _candle_closed_between(last: datetime, now: datetime, tf_hours: int) -> bool:
    last_period = int(last.timestamp()) // (tf_hours * 3600)
    now_period = int(now.timestamp()) // (tf_hours * 3600)
    return now_period > last_period


def _detect_closed_timeframes(last_tick: datetime, now: datetime) -> set:
    closed = set()
    if _candle_closed_between(last_tick, now, 1):
        closed.add("1h")
    if _candle_closed_between(last_tick, now, 4):
        closed.add("4h")
    return closed


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


def _latest_candle_for_stream(stream: dict):
    tf = stream["parameters"].get("primary_timeframe", "1h")
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(tf, 60)

    from src.backtester.engine import load_market_data
    from src.backtester.indicators import resample_ohlcv

    now = pd.Timestamp.utcnow().replace(tzinfo=None)
    load_start = (now - pd.Timedelta(hours=tf_minutes / 60 * 10)).strftime("%Y-%m-%d")
    df_raw = load_market_data(load_start)
    if df_raw.empty:
        return None

    df = resample_ohlcv(df_raw, tf) if tf != "15m" else df_raw

    candle_duration = pd.Timedelta(minutes=tf_minutes)
    df = df[df.index + candle_duration <= now]

    if df.empty:
        return None

    last = df.iloc[-1]
    return {"close": float(last["close"]), "low": float(last["low"])}


def _preflight_check(conn) -> list:
    """Verify data freshness before trading. Returns list of failure reasons."""
    from datetime import date
    issues = []

    row = conn.execute(text("SELECT MAX(timestamp) FROM market_data")).fetchone()
    if row[0] is None:
        issues.append("market_data is empty")
    else:
        latest = row[0]
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        if age_min > 120:
            issues.append(f"market_data stale ({int(age_min)}m since last candle)")

    row = conn.execute(text("SELECT MAX(date) FROM sentiment_data")).fetchone()
    if row[0] is None:
        issues.append("sentiment_data is empty")
    else:
        from datetime import date
        days_old = (date.today() - row[0]).days
        if days_old > 2:
            issues.append(f"sentiment_data stale ({days_old}d since last entry)")

    return issues


def _read_last_run(conn) -> datetime:
    row = conn.execute(text("SELECT last_run_at FROM live.executor_state WHERE id = 1")).fetchone()
    if row is None:
        return datetime.now(timezone.utc)
    ts = row.last_run_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _write_last_run(conn, now: datetime) -> None:
    conn.execute(
        text("UPDATE live.executor_state SET last_run_at = :now WHERE id = 1"),
        {"now": now},
    )


def _log_tick(conn, last_tick: datetime, closed_tfs: set, open_count: int,
              pending_count: int, signals_fired: list, entries_placed: int,
              fills: int, expirations: int, stops_triggered: int,
              error: str = None) -> None:
    conn.execute(text(
        "DELETE FROM live.executor_runs WHERE ran_at < now() - interval '90 days'"
    ))
    conn.execute(text("""
        INSERT INTO live.executor_runs
            (last_tick_at, closed_tfs, open_lots, pending_lots, signals_fired,
             entries_placed, fills, expirations, stops_triggered, error)
        VALUES
            (:last_tick, :closed_tfs, :open, :pending, :signals,
             :entries, :fills, :expirations, :stops, :error)
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
    })


def tick(conn, streams: dict, kraken: KrakenClient, last_tick: datetime,
         now: datetime, dry_run: bool) -> None:
    closed_tfs = _detect_closed_timeframes(last_tick, now)
    open_count = conn.execute(text("SELECT COUNT(*) FROM live.lots WHERE status = 'OPEN'")).scalar()
    pending_count = conn.execute(text("SELECT COUNT(*) FROM live.lots WHERE status = 'PENDING'")).scalar()
    log.info(
        f"Tick — last_run={last_tick.strftime('%Y-%m-%d %H:%M')} "
        f"closed_tfs={closed_tfs or 'none'} "
        f"open={open_count} pending={pending_count}"
        f"{' [DRY RUN]' if dry_run else ''}"
    )

    signals_fired = []
    entries_placed = 0

    candle_row = {}
    for stream_id, stream in streams.items():
        tf = stream["parameters"].get("primary_timeframe", "1h")
        if tf in closed_tfs:
            candle = _latest_candle_for_stream(stream)
            if candle:
                candle_row[stream_id] = candle

    if closed_tfs:
        for stream_id, stream in streams.items():
            tf = stream["parameters"].get("primary_timeframe", "1h")
            if tf not in closed_tfs:
                continue
            if not order_manager.slot_is_available(conn, stream_id, slot_number=1):
                log.debug(f"{stream['stream_name']}: slot occupied, skipping signal check")
                continue
            try:
                fired = signal_engine.check(stream)
            except Exception as e:
                log.error(f"Signal check failed for {stream['stream_name']}: {e}")
                continue
            if fired:
                log.info(f"Signal fired: {stream['stream_name']} — placing entry order")
                signals_fired.append(stream["stream_name"])
                order_manager.place_entry(conn, stream, kraken, dry_run)
                entries_placed += 1
            else:
                log.debug(f"{stream['stream_name']}: no signal")

    fills, expirations = order_manager.check_pending(conn, kraken, dry_run)

    stops_triggered = 0
    if closed_tfs and candle_row:
        stops_triggered = position_monitor.check_all(
            conn, streams, candle_row, closed_tfs, kraken, dry_run
        )

    _log_tick(conn, last_tick, closed_tfs, open_count, pending_count,
              signals_fired, entries_placed, fills, expirations, stops_triggered)


def run(dry_run: bool = False) -> None:
    mode = "DRY RUN" if dry_run else "LIVE"
    log.info(f"=== Forge Anchor Executor — Model {LIVE_MODEL_VERSION} [{mode}] ===")

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
        streams = _load_streams(conn)
        if not streams:
            log.error(f"No active streams found for Model {LIVE_MODEL_VERSION}. Run deploy.py first.")
            sys.exit(1)
        log.info(f"Loaded {len(streams)} streams: {[s['stream_name'] for s in streams.values()]}")

        last_tick = _read_last_run(conn)

        issues = _preflight_check(conn)
        if issues:
            msg = "Pre-flight failed: " + "; ".join(issues)
            log.error(msg)
            _log_tick(conn, last_tick, set(), 0, 0, [], 0, 0, 0, 0, error=msg)
            _write_last_run(conn, now)
            sys.exit(1)

        tick(conn, streams, kraken, last_tick, now, dry_run)
        _write_last_run(conn, now)

    log.info("=== Tick complete ===")


def main():
    parser = argparse.ArgumentParser(description="Forge Anchor live executor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without placing real Kraken orders (DB writes are real)")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
