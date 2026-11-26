import json
import os
from typing import Any

from services.coinbase_readonly_adapter_v2 import CoinbaseReadonlyAdapterV2, CoinbaseReadonlyError
from services.settings_v2 import parse_kill_switch


class CoinbaseLiveExecutionError(RuntimeError):
    pass


class CoinbaseLiveExecutionAdapterV2(CoinbaseReadonlyAdapterV2):
    """Coinbase Exchange live execution adapter.

    This class contains POST /orders plumbing. It must be reached only through
    LiveTradingServiceV2. The adapter retains its own fail-closed kill switch as
    a final protection against accidental direct live submission.
    """

    adapter_name = "coinbase_exchange_v2"
    kill_switch_env = "COINBASE_LIVE_ORDER_KILL_SWITCH"

    @classmethod
    def live_order_kill_switch_enabled(cls) -> bool:
        # Same parsing as SettingsV2.coinbase_live_order_kill_switch: on unless explicitly false.
        return parse_kill_switch(os.getenv(cls.kill_switch_env))

    @classmethod
    def assert_live_orders_not_killed(cls) -> None:
        if cls.live_order_kill_switch_enabled():
            raise CoinbaseLiveExecutionError(f"Live Coinbase order submission blocked by {cls.kill_switch_env}")

    async def _post_private(self, request_path: str, payload: dict[str, Any]) -> Any:
        self.assert_live_orders_not_killed()
        self.assert_credentials()
        body = json.dumps(payload, separators=(",", ":"))
        async with self.session_factory() as session:
            async with session.post(f"{self.base_url}{request_path}", headers=self._headers("POST", request_path, body), data=body) as response:
                data = await response.json()
                if response.status not in {200, 201}:
                    raise CoinbaseLiveExecutionError(f"Coinbase live POST {request_path} returned HTTP {response.status}: {data}")
                return data

    @staticmethod
    def market_buy_payload(symbol: str, notional_usd: float, client_order_id: str | None = None) -> dict[str, Any]:
        payload = {"type": "market", "side": "buy", "product_id": symbol, "funds": str(round(float(notional_usd), 2))}
        if client_order_id:
            payload["client_oid"] = client_order_id
        return payload

    @staticmethod
    def market_sell_payload(symbol: str, base_units: float, client_order_id: str | None = None) -> dict[str, Any]:
        payload = {"type": "market", "side": "sell", "product_id": symbol, "size": str(round(float(base_units), 12))}
        if client_order_id:
            payload["client_oid"] = client_order_id
        return payload

    @staticmethod
    def normalize_order_response(data: dict[str, Any], *, symbol: str, side: str, requested: dict[str, Any]) -> dict[str, Any]:
        raw_status = str(data.get("status", "submitted") or "submitted").lower()
        status_map = {
            "open": "acknowledged",
            "pending": "acknowledged",
            "active": "acknowledged",
            "received": "acknowledged",
            "done": "filled" if float(data.get("filled_size", 0.0) or 0.0) > 0 else "canceled",
            "settled": "filled",
            "filled": "filled",
            "rejected": "rejected",
            "failed": "failed",
            "canceled": "canceled",
            "cancelled": "canceled",
        }
        normalized_status = status_map.get(raw_status, raw_status or "submitted")
        return {
            "success": normalized_status not in {"rejected", "failed", "canceled"},
            "order_id": data.get("id"),
            "client_order_id": data.get("client_oid") or requested.get("client_order_id"),
            "status": normalized_status,
            "broker_status": raw_status,
            "symbol": symbol,
            "side": side.upper(),
            "product_id": data.get("product_id", symbol),
            "filled_price": 0.0,
            "base_units": float(data.get("filled_size", 0.0) or 0.0),
            "notional_usd": float(data.get("executed_value", 0.0) or 0.0),
            "fee_usd": float(data.get("fill_fees", 0.0) or 0.0),
            "simulation": False,
            "paper_execution": False,
            "live_execution": True,
            "execution_adapter": CoinbaseLiveExecutionAdapterV2.adapter_name,
            "raw_response": data,
            "requested": requested,
        }

    async def place_market_buy(self, symbol: str, notional_usd: float, client_order_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        requested = {"symbol": symbol, "side": "BUY", "notional_usd": round(float(notional_usd), 8), "client_order_id": client_order_id}
        payload = self.market_buy_payload(symbol, notional_usd, client_order_id=client_order_id)
        if dry_run:
            return {"success": True, "status": "dry_run", "broker_status": "dry_run", "symbol": symbol, "side": "BUY", "order_id": None, "client_order_id": client_order_id, "simulation": False, "live_execution": False, "execution_adapter": self.adapter_name, "requested": requested, "coinbase_payload_preview": payload}
        try:
            data = await self._post_private("/orders", payload)
        except CoinbaseReadonlyError as exc:
            raise CoinbaseLiveExecutionError(str(exc)) from exc
        return self.normalize_order_response(data, symbol=symbol, side="BUY", requested=requested)

    async def place_market_sell(self, symbol: str, base_units: float, client_order_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        requested = {"symbol": symbol, "side": "SELL", "base_units": round(float(base_units), 12), "client_order_id": client_order_id}
        payload = self.market_sell_payload(symbol, base_units, client_order_id=client_order_id)
        if dry_run:
            return {"success": True, "status": "dry_run", "broker_status": "dry_run", "symbol": symbol, "side": "SELL", "order_id": None, "client_order_id": client_order_id, "simulation": False, "live_execution": False, "execution_adapter": self.adapter_name, "requested": requested, "coinbase_payload_preview": payload}
        try:
            data = await self._post_private("/orders", payload)
        except CoinbaseReadonlyError as exc:
            raise CoinbaseLiveExecutionError(str(exc)) from exc
        return self.normalize_order_response(data, symbol=symbol, side="SELL", requested=requested)
