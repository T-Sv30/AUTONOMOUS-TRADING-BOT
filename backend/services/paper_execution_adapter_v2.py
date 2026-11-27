import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from services.env_numbers_v2 import env_number
from services.market_data_service import MarketDataService, MarketDataUnavailable

logger = logging.getLogger(__name__)


@dataclass
class PaperExecutionConfig:
    fee_bps: float = 10.0
    extra_slippage_bps: float = 0.0
    fallback_spread_bps: float = 2.0
    min_notional_usd: float = 5.0
    min_base_units: float = 0.00000001
    quote_precision: int = 8
    base_precision: int = 12
    partial_fill_threshold_usd: float = 250.0
    partial_fill_ratio: float = 0.65
    reject_unknown_symbols: bool = True
    latency_ms: int = 0


class PaperExecutionAdapterV2:
    """Deterministic paper execution adapter.

    This adapter models market-order lifecycle behavior without live exchange
    access. It uses MarketDataService for the quote, fills buys at the ask and
    sells at the bid (plus optional extra slippage), applies fees, enforces min-size/precision constraints, and can
    deterministically partial-fill larger paper orders.
    """

    SUPPORTED_SYMBOLS = {"BTC-USD", "ETH-USD"}

    def __init__(self, market_data: MarketDataService | None = None, config: PaperExecutionConfig | None = None):
        self._owns_market_data = market_data is None
        self.market_data = market_data or MarketDataService()
        self.config = config or self.from_env()

    async def close(self) -> None:
        """Close the market data session, but only if this adapter created it."""
        if self._owns_market_data:
            await self.market_data.close()

    @staticmethod
    def from_env() -> PaperExecutionConfig:
        def env_float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        def env_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        for removed in ("PAPER_BUY_SLIPPAGE_BPS", "PAPER_SELL_SLIPPAGE_BPS"):
            if os.getenv(removed) is not None:
                logger.warning(
                    "%s was removed and is ignored: paper fills now price at the bid and ask. Use PAPER_EXTRA_SLIPPAGE_BPS instead.",
                    removed,
                )

        return PaperExecutionConfig(
            fee_bps=env_float("PAPER_FEE_BPS", 10.0),
            extra_slippage_bps=float(env_number("PAPER_EXTRA_SLIPPAGE_BPS")),
            fallback_spread_bps=float(env_number("PAPER_FALLBACK_SPREAD_BPS")),
            min_notional_usd=env_float("PAPER_MIN_NOTIONAL_USD", 5.0),
            partial_fill_threshold_usd=env_float("PAPER_PARTIAL_FILL_THRESHOLD_USD", 250.0),
            partial_fill_ratio=env_float("PAPER_PARTIAL_FILL_RATIO", 0.65),
            latency_ms=env_int("PAPER_LATENCY_MS", 0),
        )

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _stable_order_id(prefix: str, symbol: str, client_order_id: str | None) -> str:
        if client_order_id:
            digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:16]
            return f"paper_{prefix}_{symbol.replace('-', '').lower()}_{digest}"
        millis = int(datetime.now(UTC).timestamp() * 1000)
        return f"paper_{prefix}_{symbol.replace('-', '').lower()}_{millis}"

    @staticmethod
    def _event(status: str, message: str) -> dict[str, Any]:
        return {"status": status, "message": message, "timestamp": PaperExecutionAdapterV2.utc_now()}

    def _reject(self, *, prefix: str, symbol: str, client_order_id: str | None, reason: str, requested: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": False,
            "order_id": self._stable_order_id(prefix, symbol, client_order_id),
            "client_order_id": client_order_id,
            "status": "rejected",
            "reject_reason": reason,
            "filled_price": 0.0,
            "base_units": 0.0,
            "notional_usd": 0.0,
            "fee_usd": 0.0,
            "simulation": True,
            "paper_execution": True,
            "execution_adapter": "paper_v2",
            "requested": requested,
            "config": asdict(self.config),
            "lifecycle_events": [
                self._event("created", "paper order accepted by local simulator"),
                self._event("rejected", reason),
            ],
        }

    def _is_supported_symbol(self, symbol: str) -> bool:
        return symbol in self.SUPPORTED_SYMBOLS or not self.config.reject_unknown_symbols

    def _partial_fill_ratio(self, notional_usd: float) -> float:
        if notional_usd >= self.config.partial_fill_threshold_usd:
            return max(0.0, min(1.0, self.config.partial_fill_ratio))
        return 1.0

    async def _reference_quote(self, symbol: str) -> dict[str, Any]:
        try:
            current = await self.market_data.get_current_price(symbol)
        except MarketDataUnavailable:
            raise
        except Exception as exc:
            raise MarketDataUnavailable(f"Paper execution could not resolve reference price for {symbol}") from exc
        price = float(current.get("price", 0.0) or 0.0)
        if price <= 0:
            raise MarketDataUnavailable(f"Paper execution received invalid reference price for {symbol}")
        bid, ask = current.get("bid"), current.get("ask")
        if bid is not None and ask is not None and 0 < float(bid) < float(ask):
            return {"price": price, "bid": float(bid), "ask": float(ask), "price_basis": "quote"}
        half_spread = self.config.fallback_spread_bps / 2 / 10000.0
        return {"price": price, "bid": price * (1 - half_spread), "ask": price * (1 + half_spread), "price_basis": "synthetic_spread"}

    async def place_market_buy(self, symbol: str, notional_usd: float, client_order_id: str | None = None) -> dict[str, Any]:
        requested_notional = round(float(notional_usd), self.config.quote_precision)
        requested = {"symbol": symbol, "side": "BUY", "notional_usd": requested_notional}
        if not self._is_supported_symbol(symbol):
            return self._reject(prefix="buy", symbol=symbol, client_order_id=client_order_id, reason="unsupported symbol", requested=requested)
        if requested_notional < self.config.min_notional_usd:
            return self._reject(prefix="buy", symbol=symbol, client_order_id=client_order_id, reason="notional below minimum", requested=requested)

        try:
            quote = await self._reference_quote(symbol)
        except MarketDataUnavailable as exc:
            return self._reject(prefix="buy", symbol=symbol, client_order_id=client_order_id, reason=str(exc), requested=requested)

        reference_price = quote["price"]
        fill_ratio = self._partial_fill_ratio(requested_notional)
        filled_notional = requested_notional * fill_ratio
        fee_usd = round(filled_notional * (self.config.fee_bps / 10000.0), self.config.quote_precision)
        spendable = max(0.0, filled_notional - fee_usd)
        filled_price = quote["ask"] * (1 + self.config.extra_slippage_bps / 10000.0)
        base_units = round(spendable / filled_price if filled_price else 0.0, self.config.base_precision)
        status = "filled" if fill_ratio >= 1.0 else "partially_filled"
        success = base_units >= self.config.min_base_units
        if not success:
            return self._reject(prefix="buy", symbol=symbol, client_order_id=client_order_id, reason="filled base units below minimum", requested=requested)

        return {
            "success": True,
            "order_id": self._stable_order_id("buy", symbol, client_order_id),
            "client_order_id": client_order_id,
            "status": status,
            "filled_price": round(filled_price, self.config.quote_precision),
            "reference_price": round(reference_price, self.config.quote_precision),
            "base_units": base_units,
            "notional_usd": round(filled_notional, self.config.quote_precision),
            "requested_notional_usd": requested_notional,
            "unfilled_notional_usd": round(requested_notional - filled_notional, self.config.quote_precision),
            "fee_usd": fee_usd,
            "price_basis": quote["price_basis"],
            "slippage_bps": self.config.extra_slippage_bps,
            "fill_ratio": round(fill_ratio, 8),
            "simulation": True,
            "paper_execution": True,
            "execution_adapter": "paper_v2",
            "requested": requested,
            "config": asdict(self.config),
            "lifecycle_events": [
                self._event("created", "paper market buy created"),
                self._event("accepted", "paper market buy accepted"),
                self._event(status, f"paper market buy {status}"),
            ],
        }

    async def place_market_sell(self, symbol: str, base_units: float, client_order_id: str | None = None) -> dict[str, Any]:
        requested_units = round(float(base_units), self.config.base_precision)
        requested = {"symbol": symbol, "side": "SELL", "base_units": requested_units}
        if not self._is_supported_symbol(symbol):
            return self._reject(prefix="sell", symbol=symbol, client_order_id=client_order_id, reason="unsupported symbol", requested=requested)
        if requested_units < self.config.min_base_units:
            return self._reject(prefix="sell", symbol=symbol, client_order_id=client_order_id, reason="base units below minimum", requested=requested)

        try:
            quote = await self._reference_quote(symbol)
        except MarketDataUnavailable as exc:
            return self._reject(prefix="sell", symbol=symbol, client_order_id=client_order_id, reason=str(exc), requested=requested)

        reference_price = quote["price"]
        estimated_notional = requested_units * reference_price
        fill_ratio = self._partial_fill_ratio(estimated_notional)
        filled_units = round(requested_units * fill_ratio, self.config.base_precision)
        filled_price = quote["bid"] * (1 - self.config.extra_slippage_bps / 10000.0)
        gross_notional = filled_units * filled_price
        fee_usd = round(gross_notional * (self.config.fee_bps / 10000.0), self.config.quote_precision)
        net_notional = round(max(0.0, gross_notional - fee_usd), self.config.quote_precision)
        status = "filled" if fill_ratio >= 1.0 else "partially_filled"

        return {
            "success": True,
            "order_id": self._stable_order_id("sell", symbol, client_order_id),
            "client_order_id": client_order_id,
            "status": status,
            "filled_price": round(filled_price, self.config.quote_precision),
            "reference_price": round(reference_price, self.config.quote_precision),
            "base_units": filled_units,
            "requested_base_units": requested_units,
            "unfilled_base_units": round(requested_units - filled_units, self.config.base_precision),
            "notional_usd": net_notional,
            "gross_notional_usd": round(gross_notional, self.config.quote_precision),
            "fee_usd": fee_usd,
            "price_basis": quote["price_basis"],
            "slippage_bps": self.config.extra_slippage_bps,
            "fill_ratio": round(fill_ratio, 8),
            "simulation": True,
            "paper_execution": True,
            "execution_adapter": "paper_v2",
            "requested": requested,
            "config": asdict(self.config),
            "lifecycle_events": [
                self._event("created", "paper market sell created"),
                self._event("accepted", "paper market sell accepted"),
                self._event(status, f"paper market sell {status}"),
            ],
        }
