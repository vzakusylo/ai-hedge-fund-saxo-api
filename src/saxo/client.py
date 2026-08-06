"""
Saxo Bank OpenAPI client for account info, instrument lookup, and order placement.
Uses the simulation environment (sim/openapi). Switch BASE_URL for live trading.
"""
import os
import requests
from typing import Optional


# Simulation environment - change to https://gateway.saxobank.com/openapi for live
BASE_URL = "https://gateway.saxobank.com/sim/openapi"


class SaxoClient:
    def __init__(self, access_token: Optional[str] = None):
        self.token = access_token or os.environ.get("SAXO_ACCESS_TOKEN", "")
        if not self.token:
            raise ValueError("SAXO_ACCESS_TOKEN is not set")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.session.get(f"{BASE_URL}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self.session.post(f"{BASE_URL}{path}", json=body)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    #  Account                                                             #
    # ------------------------------------------------------------------ #

    def get_accounts(self) -> list[dict]:
        """Return list of accounts for the authenticated user."""
        data = self._get("/port/v1/accounts/me")
        return data.get("Data", [])

    def get_account_key(self) -> str:
        """Return the first account key available."""
        accounts = self.get_accounts()
        if not accounts:
            raise RuntimeError("No accounts found for this token")
        return accounts[0]["AccountKey"]

    def get_balance(self, account_key: str) -> dict:
        """Return account balance/equity info."""
        # Saxo /port/v1/balances requires ClientKey for the summary balance
        accounts = self.get_accounts()
        client_key = accounts[0]["ClientKey"] if accounts else account_key
        return self._get("/port/v1/balances", params={"ClientKey": client_key})

    def get_positions(self, account_key: str) -> list[dict]:
        """Return open positions for the account."""
        accounts = self.get_accounts()
        client_key = accounts[0]["ClientKey"] if accounts else account_key
        data = self._get("/port/v1/positions", params={"ClientKey": client_key})
        return data.get("Data", [])

    def get_open_orders(self, account_key: str) -> list[dict]:
        """Return open orders for the account."""
        accounts = self.get_accounts()
        client_key = accounts[0]["ClientKey"] if accounts else account_key
        data = self._get("/port/v1/orders", params={"ClientKey": client_key})
        return data.get("Data", [])

    # ------------------------------------------------------------------ #
    #  Instruments                                                         #
    # ------------------------------------------------------------------ #

    def find_instrument(self, ticker: str, asset_type: str = "Stock") -> Optional[dict]:
        """
        Look up a Saxo instrument by ticker symbol.
        Returns the first match or None.
        """
        result = self._get("/ref/v1/instruments", params={
            "Keywords": ticker,
            "AssetTypes": asset_type,
            "$top": 5,
        })
        instruments = result.get("Data", [])
        # Try exact or prefix symbol match (Saxo symbols can have exchange suffix, e.g. AAPL:xnas)
        for inst in instruments:
            sym = inst.get("Symbol", "").upper().split(":")[0]
            if sym == ticker.upper():
                return inst
        return instruments[0] if instruments else None

    def get_instrument_price(self, uic: int, asset_type: str = "Stock") -> Optional[float]:
        """Return the mid/ask price for an instrument by UIC."""
        try:
            data = self._get("/trade/v1/infoprices", params={
                "Uic": uic,
                "AssetType": asset_type,
                "FieldGroups": "Quote",
            })
            quote = data.get("Quote", {})
            ask = quote.get("Ask")
            bid = quote.get("Bid")
            if ask and bid:
                return (ask + bid) / 2
            return ask or bid
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Orders                                                              #
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        account_key: str,
        uic: int,
        asset_type: str,
        buy_sell: str,       # "Buy" or "Sell"
        quantity: int,
        order_type: str = "Market",
        price: Optional[float] = None,
    ) -> dict:
        """
        Place a Market or Limit order.
        Returns the order response from Saxo API.
        """
        body = {
            "AccountKey": account_key,
            "AssetType": asset_type,
            "Uic": uic,
            "BuySell": buy_sell,
            "Amount": quantity,
            "OrderType": order_type,
            "OrderDuration": {"DurationType": "DayOrder"},
            "ManualOrder": False,
        }
        if order_type == "Limit" and price is not None:
            body["Price"] = price

        return self._post("/trade/v2/orders", body)

    def precheck_order(
        self,
        account_key: str,
        uic: int,
        asset_type: str,
        buy_sell: str,
        quantity: int,
        order_type: str = "Market",
        price: Optional[float] = None,
    ) -> dict:
        """Pre-check an order before placing it (dry-run)."""
        body = {
            "AccountKey": account_key,
            "AssetType": asset_type,
            "Uic": uic,
            "BuySell": buy_sell,
            "Amount": quantity,
            "OrderType": order_type,
            "OrderDuration": {"DurationType": "DayOrder"},
            "ManualOrder": False,
        }
        if order_type == "Limit" and price is not None:
            body["Price"] = price

        return self._post("/trade/v2/orders/precheck", body)
