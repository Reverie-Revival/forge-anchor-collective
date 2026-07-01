"""
Live trading executor — main loop for Model 1.

Runs every 60 seconds. On each tick:
  1. Detect which candle timeframes (1h, 4h) closed since last tick
  2. For streams whose timeframe closed: check for new signals → place entry orders
  3. Poll all PENDING lots for fills / expiry
  4. Check trailing stops on all OPEN lots

Usage:
    python -m src.live.executor              # live mode — real Kraken orders
    python -m src.live.executor --dry-run    # no real orders; DB writes are real
"""
import argparse
import logging
import os
import sys
import time
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
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("live_executor.log"),
    ],
)
log = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 60
LIVE_MODEL_VERSION = 1


def _get_engine():
    url = os.getenv("DATABASE_URL", "postgresql://localhost/forge_anchor")
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def _candle_closed_between(last: datetime, now: datetime, tf_hours: int) -> bool:
    """Return True if at least one candle boundary for tf_hours crossed between last and now."""
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
    """Load all live streams for Model 1. Returns {stream_id: stream_dict}."""
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


def _latest_candle_for_stream(stream: dict) -> dict | None:
    """
    Fetch the most recently completed candle for a stream's timeframe from market_data.
    Returns {'close': float, 'low': float} or None.
    """
    tf = stream["parameters"].get("primary_timeframe", "1h")
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(tf, 60)

    # Load recent data and resample
    from src.backtester.engine import load_market_data
    from src.backtester.indicators import resample_ohlcv

    now = pd.Timestamp.utcnow()
    load_start = (now - pd.Timedelta(hours=tf_minutes / 60 * 10)).strftime("%Y-%m-%d")
    df_raw = load_market_data(load_start)
    if df_raw.empty:
        return None

    df = resample_ohlcv(df_raw, tf) if tf != "15m" else df_raw

    # Drop in-progress candle
    candle_duration = pd.Timedelta(minutes=tf_minutes)
    df = df[df.index + candle_duration <= now]

    if df.empty:
        return None

    last = df.iloc[-1]
    return {"close": float(last["close"]), "low": float(last["low"])}


def tick(conn, streams: dict, kraken: KrakenClient, last_tick: datetime,
         now: datetime, dry_run: bool) -> None:
    closed_tfs = _detect_closed_timeframes(last_tick, now)
    open_count = conn.execute(text("SELECT COUNT(*) FROM live.lots WHERE status = 'OPEN'")).scalar()
    pending_count = conn.execute(text("SELECT COUNT(*) FROM live.lots WHERE status = 'PENDING'")).scalar()
    log.info(
        f"Tick — closed_tfs={closed_tfs or 'none'} "
        f"open={open_count} pending={pending_count}"
        f"{' [DRY RUN]' if dry_run else ''}"
    )

    # Build candle data for all streams whose timeframe closed
    candle_row = {}
    for stream_id, stream in streams.items():
        tf = stream["parameters"].get("primary_timeframe", "1h")
        if tf in closed_tfs:
            candle = _latest_candle_for_stream(stream)
            if candle:
                candle_row[stream_id] = candle

    # Signal check → entry orders
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
                order_manager.place_entry(conn, stream, kraken, dry_run)
            else:
                log.debug(f"{stream['stream_name']}: no signal")

    # Poll pending orders
    order_manager.check_pending(conn, kraken, dry_run)

    # Check trailing stops
    if closed_tfs and candle_row:
        position_monitor.check_all(conn, streams, candle_row, closed_tfs, kraken, dry_run)


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

    with engine.connect() as conn:
        streams = _load_streams(conn)
        if not streams:
            log.error(f"No active streams found for Model {LIVE_MODEL_VERSION}. Run deploy.py first.")
            sys.exit(1)
        log.info(f"Loaded {len(streams)} streams: {[s['stream_name'] for s in streams.values()]}")

    last_tick = datetime.now(timezone.utc)

    while True:
        time.sleep(TICK_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        try:
            with engine.begin() as conn:
                streams = _load_streams(conn)
                tick(conn, streams, kraken, last_tick, now, dry_run)
        except Exception as e:
            log.error(f"Tick error (continuing): {e}", exc_info=True)
        last_tick = now


def main():
    parser = argparse.ArgumentParser(description="Forge Anchor live executor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without placing real Kraken orders (DB writes are real)")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
