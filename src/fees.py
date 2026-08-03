"""
Single source of truth for Kraken's real fee rates -- used by both the
live executors (src/live/order_manager.py) and the backtester
(src/backtester/engine.py). Previously these were two separate,
independently-hardcoded copies of the same numbers, which is exactly how
they drifted from reality in the first place (see HANDOFF.md, 2026-08-03).

Kraken's lowest volume tier (confirmed via TradeVolume API, tiervolume=0,
nextvolume=$2500/30d) -- every account here starts at this tier. Fees step
down as 30-day trading volume crosses $2,500 (taker -> 0.60% next tier).
Re-check via `kraken._api.query_private('TradeVolume', {'pair': 'XXBTZUSD'})`
if volume has grown, rather than assuming this is still current --
src/live/fee_check.py does this automatically every healthcheck cycle and
alerts on drift.
"""

MAKER_FEE = 0.0040  # 0.40% -- limit entry
TAKER_FEE = 0.0080  # 0.80% -- market exit
