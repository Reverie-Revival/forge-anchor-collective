"""
Shared FakeKraken stub for tests/live/. Single source of truth so the fill
simulation logic can't drift between test files (test_blended_executor_tick.py
briefly had its own un-upgraded copy that silently ignored next_fill_mode --
exactly this kind of duplication caused that bug).
"""


class FakeKraken:
    """
    Stub KrakenClient. Default behavior: every order fills instantly and
    fully at the submitted price (`next_fill_mode = "full"`).

    Set `next_fill_mode` before calling place_order to simulate other real
    Kraken behaviors:
      "full"    -- fills completely the moment it's placed (the default)
      "none"    -- never fills; stays "open" with vol_exec=0 until cancelled,
                   at which point it correctly reports vol_exec=0
      "partial" -- fills `next_partial_fraction` of the requested volume and
                   stays "open" (not "closed") until cancelled -- mirrors a
                   real partial fill sitting on the book. cancel_order locks
                   in whatever partial amount filled (status -> "canceled",
                   vol_exec stays > 0) -- cancelling does NOT undo a fill.
    """

    def __init__(self):
        self.orders = {}
        self._next_id = 1
        self._next_price = 50000.0
        self.next_fill_mode = "full"
        self.next_partial_fraction = 0.4

    def get_ticker_price(self):
        return self._next_price

    def place_order(self, side, volume_btc, price_usd=None, order_type="limit"):
        txid = f"FAKE-{self._next_id}"
        self._next_id += 1
        fill_price = price_usd if price_usd is not None else self._next_price
        mode = self.next_fill_mode
        self.next_fill_mode = "full"   # reset to the default for the next order

        if mode == "full":
            self.orders[txid] = {"status": "closed", "vol_exec": f"{volume_btc:.8f}", "price": f"{fill_price:.2f}"}
        elif mode == "none":
            self.orders[txid] = {"status": "open", "vol_exec": "0.00000000", "price": "0.00", "_requested": volume_btc}
        elif mode == "partial":
            filled = volume_btc * self.next_partial_fraction
            self.orders[txid] = {"status": "open", "vol_exec": f"{filled:.8f}", "price": f"{fill_price:.2f}", "_requested": volume_btc}
        return txid

    def get_order_status(self, txid):
        order = dict(self.orders[txid])
        order.pop("_requested", None)
        return order

    def cancel_order(self, txid):
        order = self.orders[txid]
        if order["status"] == "open":
            order["status"] = "canceled"   # vol_exec (0 or partial) is left exactly as-is
