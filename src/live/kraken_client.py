import os
import krakenex
from dotenv import load_dotenv

load_dotenv()


class KrakenClient:
    """
    Thin authenticated wrapper around the Kraken REST API.
    API key must have Create Order permission only — no withdrawal.
    """

    def __init__(self):
        self._api = krakenex.API()
        self._api.key = os.getenv("KRAKEN_API_KEY", "")
        self._api.secret = os.getenv("KRAKEN_API_SECRET", "")

    def validate_connection(self) -> dict:
        """Verify API keys work. Returns balance dict or raises on auth failure."""
        resp = self._api.query_private("Balance")
        if resp.get("error"):
            raise RuntimeError(f"Kraken auth failed: {resp['error']}")
        return resp["result"]

    def get_balance(self) -> dict:
        """Return dict of asset → available balance string (e.g. {'ZUSD': '99.50', 'XXBT': '0.00100'})."""
        resp = self._api.query_private("Balance")
        if resp.get("error"):
            raise RuntimeError(f"Kraken Balance error: {resp['error']}")
        return resp["result"]

    def place_order(self, side: str, volume_btc: float, price_usd: float = None,
                    order_type: str = "limit") -> str:
        """
        Place a BTC/USD order. Returns Kraken txid.
        side: 'buy' or 'sell'
        order_type: 'limit' (entry, maker fee) or 'market' (exit, taker fee)
        """
        params = {
            "pair": "XBTUSD",
            "type": side,
            "ordertype": order_type,
            "volume": f"{volume_btc:.8f}",
        }
        if order_type == "limit":
            if price_usd is None:
                raise ValueError("price_usd required for limit orders")
            params["price"] = f"{price_usd:.2f}"

        resp = self._api.query_private("AddOrder", params)
        if resp.get("error"):
            raise RuntimeError(f"Kraken AddOrder error: {resp['error']}")
        txids = resp["result"].get("txid", [])
        if not txids:
            raise RuntimeError("Kraken returned no txid")
        return txids[0]

    def cancel_order(self, txid: str) -> None:
        """Cancel a pending order. Silently ignores already-closed orders."""
        resp = self._api.query_private("CancelOrder", {"txid": txid})
        if resp.get("error"):
            # EOrder:Unknown order is not an error — already filled or cancelled
            if any("Unknown order" in e for e in resp["error"]):
                return
            raise RuntimeError(f"Kraken CancelOrder error: {resp['error']}")

    def get_order_status(self, txid: str) -> dict:
        """
        Return order status dict for txid. Key fields:
          status:   'pending' | 'open' | 'closed' | 'canceled' | 'expired'
          vol_exec: volume filled so far (string)
          price:    average fill price (string)
        Returns empty dict if txid not found.
        """
        resp = self._api.query_private("QueryOrders", {"txid": txid, "trades": True})
        if resp.get("error"):
            raise RuntimeError(f"Kraken QueryOrders error: {resp['error']}")
        return resp["result"].get(txid, {})

    def get_ticker_price(self) -> float:
        """Return current BTC/USD last trade price."""
        resp = self._api.query_public("Ticker", {"pair": "XBTUSD"})
        if resp.get("error"):
            raise RuntimeError(f"Kraken Ticker error: {resp['error']}")
        return float(resp["result"]["XXBTZUSD"]["c"][0])
