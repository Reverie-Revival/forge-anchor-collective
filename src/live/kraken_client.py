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
        order_type: 'limit' (maker fee -- entries, and blended's armed/trailing-stop
        exits) or 'market' (taker fee -- capitulation and Model 1's stop-loss exits)
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
          fee:      real fee charged on this order so far, in USD (string) —
                    a dollar amount, NOT a rate like get_fee_tier()'s output.
        Returns empty dict if txid not found in either QueryOrders or TradesHistory.

        Note: Kraken's QueryOrders can return empty for orders that filled
        immediately (taker fills). Falls back to TradesHistory to detect fills.
        A single order can fill as multiple partial trades against different
        resting counter-orders (confirmed live 2026-08-17: a $25 maker buy
        filled as two trades, 0.00032317 + 0.00007097 BTC) — the fallback
        aggregates every trade matching this ordertxid rather than returning
        the first, or it silently under-reports the position.
        """
        resp = self._api.query_private("QueryOrders", {"txid": txid, "trades": True})
        if resp.get("error"):
            raise RuntimeError(f"Kraken QueryOrders error: {resp['error']}")
        order = resp["result"].get(txid, {})
        if order:
            return order

        # QueryOrders missed it — check TradesHistory for matching fills
        resp2 = self._api.query_private("TradesHistory")
        if resp2.get("error"):
            return {}
        matches = [t for t in resp2["result"].get("trades", {}).values()
                   if t.get("ordertxid") == txid]
        if not matches:
            return {}
        total_vol = sum(float(t["vol"]) for t in matches)
        total_cost = sum(float(t["cost"]) for t in matches)
        total_fee = sum(float(t["fee"]) for t in matches)
        return {
            "status": "closed",
            "vol_exec": f"{total_vol:.8f}",
            "price": f"{total_cost / total_vol:.5f}" if total_vol else "0",
            "fee": f"{total_fee:.5f}",
        }

    def get_ticker_price(self) -> float:
        """Return current BTC/USD last trade price."""
        resp = self._api.query_public("Ticker", {"pair": "XBTUSD"})
        if resp.get("error"):
            raise RuntimeError(f"Kraken Ticker error: {resp['error']}")
        return float(resp["result"]["XXBTZUSD"]["c"][0])

    def get_fee_tier(self) -> dict:
        """
        Return this account's real current XBT/USD fee tier as decimals
        (e.g. 0.0040, not "0.4000"). Ground truth for whatever MAKER_FEE/
        TAKER_FEE assume in code -- fees are tiered by 30-day trading volume
        and step down as volume grows, so this can't be hardcoded once.
        """
        resp = self._api.query_private("TradeVolume", {"pair": "XXBTZUSD"})
        if resp.get("error"):
            raise RuntimeError(f"Kraken TradeVolume error: {resp['error']}")
        result = resp["result"]
        taker = result["fees"]["XXBTZUSD"]
        maker = result["fees_maker"]["XXBTZUSD"]
        return {
            "maker_fee":   float(maker["fee"]) / 100,
            "taker_fee":   float(taker["fee"]) / 100,
            "tier_volume": float(taker["tiervolume"]),
            "next_volume": float(taker["nextvolume"]),
        }
