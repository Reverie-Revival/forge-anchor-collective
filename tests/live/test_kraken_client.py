"""
get_order_status's TradesHistory fallback (used when QueryOrders returns {}
for an already-filled order, e.g. taker fills) must aggregate every trade
matching the ordertxid. Confirmed live 2026-08-17: Model 2's Volume Raider
$25 order filled as two separate maker trades (0.00032317 + 0.00007097 BTC),
and taking only the first trade under-recorded the position by ~82% --
0.00032317 BTC bought with real money went untracked by any lot, unprotected
by the trailing stop. See docs/decisions or HANDOFF.md for the incident.
"""
from unittest.mock import MagicMock

from src.live.kraken_client import KrakenClient


def _client_with_api(query_private_side_effect):
    client = KrakenClient.__new__(KrakenClient)
    client._api = MagicMock()
    client._api.query_private.side_effect = query_private_side_effect
    return client


def test_get_order_status_aggregates_multiple_trades():
    def side_effect(endpoint, params=None):
        if endpoint == "QueryOrders":
            return {"error": [], "result": {}}
        if endpoint == "TradesHistory":
            return {
                "error": [],
                "result": {
                    "trades": {
                        "t1": {"ordertxid": "TXID1", "vol": "0.00032317",
                               "cost": "20.49845", "fee": "0.08199"},
                        "t2": {"ordertxid": "TXID1", "vol": "0.00007097",
                               "cost": "4.50158", "fee": "0.01801"},
                        "t3": {"ordertxid": "OTHER", "vol": "1.0",
                               "cost": "50000", "fee": "200"},
                    }
                },
            }
        raise AssertionError(f"unexpected endpoint {endpoint}")

    client = _client_with_api(side_effect)
    status = client.get_order_status("TXID1")

    assert status["status"] == "closed"
    assert float(status["vol_exec"]) == 0.00039414
    assert round(float(status["fee"]), 5) == 0.10000
    assert abs(float(status["price"]) - 63429.30) < 0.05


def test_get_order_status_single_trade_matches_prior_behavior():
    def side_effect(endpoint, params=None):
        if endpoint == "QueryOrders":
            return {"error": [], "result": {}}
        if endpoint == "TradesHistory":
            return {
                "error": [],
                "result": {
                    "trades": {
                        "t1": {"ordertxid": "TXID2", "vol": "0.00053149",
                               "cost": "33.32979", "fee": "0.13332"},
                    }
                },
            }
        raise AssertionError(f"unexpected endpoint {endpoint}")

    client = _client_with_api(side_effect)
    status = client.get_order_status("TXID2")

    assert status["vol_exec"] == "0.00053149"
    assert round(float(status["price"]), 5) == round(33.32979 / 0.00053149, 5)


def test_get_order_status_no_match_returns_empty():
    def side_effect(endpoint, params=None):
        if endpoint == "QueryOrders":
            return {"error": [], "result": {}}
        if endpoint == "TradesHistory":
            return {"error": [], "result": {"trades": {}}}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    client = _client_with_api(side_effect)
    assert client.get_order_status("NOPE") == {}


def test_get_order_status_prefers_query_orders_when_present():
    def side_effect(endpoint, params=None):
        if endpoint == "QueryOrders":
            return {"error": [], "result": {"TXID3": {"status": "open", "vol_exec": "0.1"}}}
        raise AssertionError("TradesHistory should not be called when QueryOrders has the order")

    client = _client_with_api(side_effect)
    status = client.get_order_status("TXID3")
    assert status == {"status": "open", "vol_exec": "0.1"}
