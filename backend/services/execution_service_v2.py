import uuid
from datetime import UTC, datetime
from typing import Any

from services.trading_service_v2 import TradingServiceV2


class ExecutionServiceV2:
    def __init__(self, trading_service: TradingServiceV2 | None = None):
        self.trading_service = trading_service or TradingServiceV2()

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def build_client_order_id(prefix: str, symbol: str, idempotency_key: str | None = None) -> str:
        if idempotency_key:
            stable = uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:16]
            return f"{prefix.lower()}_{symbol.replace('-', '').lower()}_{stable}"
        return f"{prefix.lower()}_{symbol.replace('-', '').lower()}_{uuid.uuid4().hex[:16]}"

    async def buy(
        self,
        symbol: str,
        notional_usd: float,
        signal_snapshot: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        client_order_id = self.build_client_order_id("buy", symbol, idempotency_key)
        result = await self.trading_service.place_market_buy(
            symbol=symbol,
            notional_usd=notional_usd,
            client_order_id=client_order_id,
        )
        result["client_order_id"] = client_order_id
        result["idempotency_key"] = idempotency_key
        result["requested_notional_usd"] = round(float(notional_usd), 8)
        result["signal_snapshot"] = signal_snapshot or {}
        result["timestamp"] = self.utc_now()
        return result

    async def sell(
        self,
        symbol: str,
        base_units: float,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        client_order_id = self.build_client_order_id("sell", symbol, idempotency_key)
        result = await self.trading_service.place_market_sell(
            symbol=symbol,
            base_units=base_units,
            client_order_id=client_order_id,
        )
        result["client_order_id"] = client_order_id
        result["idempotency_key"] = idempotency_key
        result["requested_base_units"] = round(float(base_units), 12)
        result["reason"] = reason
        result["timestamp"] = self.utc_now()
        return result
