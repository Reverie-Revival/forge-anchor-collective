"""
Fee-drift safeguard. Kraken's real fee tier is confirmed live via the
TradeVolume API, never assumed -- but nothing previously checked that
MAKER_FEE/TAKER_FEE in code still matched reality. This surfaced once,
the hard way (Model 3's first real trade filled at double the assumed
taker rate). Call check_fee_drift() from a periodic job (both healthcheck.py
and blended_healthcheck.py run every 2h and already have alert plumbing) to
catch the next drift automatically instead of waiting for a real trade to
expose it again.

Both live models share one Kraken account, so the real fee TIER is the same
for both. But Model 1 and Model 3 live on separate branches (live-model-1,
live-model-3), each with its OWN copy of MAKER_FEE/TAKER_FEE in their own
order_manager.py -- these copies could drift independently of each other,
not just from Kraken's real rate. So this check must run from BOTH models'
healthcheck, not just one.

Usage:
    python -m src.live.fee_check
"""
import logging

from src.live import notifier
from src.live.kraken_client import KrakenClient
from src.live.order_manager import MAKER_FEE, TAKER_FEE

log = logging.getLogger(__name__)

# Small tolerance for float round-tripping through Kraken's string percentages
# (e.g. "0.4000" -> 0.004) -- not meant to absorb a real tier change.
_TOLERANCE = 0.0001


def check_fee_drift(client: KrakenClient = None) -> bool:
    """
    Compare Kraken's real current fee tier against MAKER_FEE/TAKER_FEE.
    Returns True if they match (or the check couldn't run), False if a real
    drift was found and an alert was fired. Never raises -- a Kraken API
    hiccup here shouldn't take down whatever's calling it (e.g. a heartbeat
    check that also needs to report executor liveness).
    """
    client = client or KrakenClient()
    try:
        tier = client.get_fee_tier()
    except Exception as e:
        log.error(f"Fee drift check failed (Kraken API error, not a drift): {e}")
        return True

    maker_drift = abs(tier["maker_fee"] - MAKER_FEE) > _TOLERANCE
    taker_drift = abs(tier["taker_fee"] - TAKER_FEE) > _TOLERANCE

    if maker_drift or taker_drift:
        log.warning(
            f"FEE DRIFT: code assumes maker {MAKER_FEE*100:.2f}%/taker {TAKER_FEE*100:.2f}%, "
            f"Kraken reports maker {tier['maker_fee']*100:.2f}%/taker {tier['taker_fee']*100:.2f}% "
            f"(30d volume ${tier['tier_volume']:,.2f}, next tier at ${tier['next_volume']:,.2f})"
        )
        notifier.alert_fee_drift(
            real_maker=tier["maker_fee"], real_taker=tier["taker_fee"],
            const_maker=MAKER_FEE, const_taker=TAKER_FEE,
            tier_volume=tier["tier_volume"], next_volume=tier["next_volume"],
        )
        return False

    log.info(
        f"Fee tier OK: maker {tier['maker_fee']*100:.2f}%/taker {tier['taker_fee']*100:.2f}% "
        f"(30d volume ${tier['tier_volume']:,.2f}, next tier at ${tier['next_volume']:,.2f})"
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
    from dotenv import load_dotenv
    load_dotenv()
    check_fee_drift()
