import asyncio
import base64
import hashlib
import hmac
import os
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import aiohttp


class CoinbaseReadonlyErrorKind(str, Enum):
    CREDENTIALS = "credentials"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    EXCHANGE_UNAVAILABLE = "exchange_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    HTTP_ERROR = "http_error"
    NETWORK = "network"
    UNKNOWN = "unknown"


class CoinbaseReadonlyError(RuntimeError):
    def __init__(self, message: str, *, kind: CoinbaseReadonlyErrorKind | str = CoinbaseReadonlyErrorKind.UNKNOWN, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.kind = str(kind.value if isinstance(kind, CoinbaseReadonlyErrorKind) else kind)
        self.status = status
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {"message": str(self), "kind": self.kind, "status": self.status, "retryable": self.retryable}


class CoinbaseReadonlyAdapterV2:
    """Coinbase Exchange readonly adapter with fail-closed reliability controls."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        passphrase: str | None = None,
        base_url: str | None = None,
        session_factory=None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("COINBASE_EXCHANGE_API_KEY")
        self.api_secret = api_secret if api_secret is not None else os.getenv("COINBASE_EXCHANGE_API_SECRET")
        self.passphrase = passphrase if passphrase is not None else os.getenv("COINBASE_EXCHANGE_PASSPHRASE")
        self.base_url = (base_url or os.getenv("COINBASE_EXCHANGE_URL", "https://api.exchange.coinbase.com")).rstrip("/")
        self.session_factory = session_factory or aiohttp.ClientSession
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None else os.getenv("COINBASE_READONLY_TIMEOUT_SECONDS", "10"))
        self.max_retries = int(max_retries if max_retries is not None else os.getenv("COINBASE_READONLY_MAX_RETRIES", "2"))

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @property
    def credentials_configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.passphrase)

    def credential_alias(self) -> str:
        if not self.api_key:
            return "not_configured"
        digest = hashlib.sha256(str(self.api_key).encode("utf-8")).hexdigest()[:12]
        return f"coinbase_key_{digest}"

    def assert_credentials(self) -> None:
        if not self.credentials_configured:
            raise CoinbaseReadonlyError(
                "Coinbase readonly credentials are not configured. Set COINBASE_EXCHANGE_API_KEY, "
                "COINBASE_EXCHANGE_API_SECRET, and COINBASE_EXCHANGE_PASSPHRASE with readonly permissions.",
                kind=CoinbaseReadonlyErrorKind.CREDENTIALS,
                retryable=False,
            )

    def _signature(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        self.assert_credentials()
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        try:
            secret = base64.b64decode(str(self.api_secret))
        except Exception:
            secret = str(self.api_secret).encode("utf-8")
        digest = hmac.new(secret, message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        timestamp = str(time.time())
        return {
            "CB-ACCESS-KEY": str(self.api_key),
            "CB-ACCESS-SIGN": self._signature(timestamp, method, request_path, body),
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-ACCESS-PASSPHRASE": str(self.passphrase),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _classify_http_error(status: int, path: str) -> CoinbaseReadonlyError:
        if status == 429:
            return CoinbaseReadonlyError(f"Coinbase readonly GET {path} rate limited", kind=CoinbaseReadonlyErrorKind.RATE_LIMIT, status=status, retryable=True)
        if status in {500, 502, 503, 504}:
            return CoinbaseReadonlyError(f"Coinbase readonly GET {path} unavailable HTTP {status}", kind=CoinbaseReadonlyErrorKind.EXCHANGE_UNAVAILABLE, status=status, retryable=True)
        if status in {401, 403}:
            return CoinbaseReadonlyError(f"Coinbase readonly GET {path} credential rejected HTTP {status}", kind=CoinbaseReadonlyErrorKind.CREDENTIALS, status=status, retryable=False)
        return CoinbaseReadonlyError(f"Coinbase readonly GET {path} returned HTTP {status}", kind=CoinbaseReadonlyErrorKind.HTTP_ERROR, status=status, retryable=False)

    async def _request_json(self, request_path: str, *, private: bool) -> Any:
        if private:
            self.assert_credentials()
        headers = self._headers("GET", request_path) if private else None
        last_error: CoinbaseReadonlyError | None = None
        attempts = max(1, self.max_retries + 1)

        for attempt in range(1, attempts + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                async with self.session_factory(timeout=timeout) as session:
                    async with session.get(f"{self.base_url}{request_path}", headers=headers) as response:
                        if response.status != 200:
                            raise self._classify_http_error(response.status, request_path)
                        try:
                            return await response.json()
                        except Exception as exc:
                            raise CoinbaseReadonlyError(f"Coinbase readonly GET {request_path} returned malformed JSON: {exc}", kind=CoinbaseReadonlyErrorKind.MALFORMED_RESPONSE, retryable=False) from exc
            except CoinbaseReadonlyError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
            except TimeoutError as exc:
                last_error = CoinbaseReadonlyError(f"Coinbase readonly GET {request_path} timed out: {exc}", kind=CoinbaseReadonlyErrorKind.TIMEOUT, retryable=True)
                if attempt >= attempts:
                    raise last_error from exc
            except aiohttp.ClientError as exc:
                last_error = CoinbaseReadonlyError(f"Coinbase readonly GET {request_path} network error: {exc}", kind=CoinbaseReadonlyErrorKind.NETWORK, retryable=True)
                if attempt >= attempts:
                    raise last_error from exc

            await asyncio.sleep(min(0.25 * attempt, 1.0))

        raise last_error or CoinbaseReadonlyError(f"Coinbase readonly GET {request_path} failed", kind=CoinbaseReadonlyErrorKind.UNKNOWN)

    async def _get_public(self, request_path: str) -> Any:
        return await self._request_json(request_path, private=False)

    async def _get_private(self, request_path: str) -> Any:
        return await self._request_json(request_path, private=True)

    async def get_product_ticker(self, symbol: str) -> dict[str, Any]:
        data = await self._get_public(f"/products/{symbol}/ticker")
        return {
            "symbol": symbol,
            "price": float(data.get("price", 0.0) or 0.0),
            "volume": float(data.get("volume", 0.0) or 0.0),
            "bid": float(data.get("bid", 0.0) or 0.0),
            "ask": float(data.get("ask", 0.0) or 0.0),
            "source": "coinbase_exchange",
            "simulation": False,
            "timestamp": self.utc_now(),
        }

    async def get_accounts(self) -> list[dict[str, Any]]:
        raw = await self._get_private("/accounts")
        if not isinstance(raw, list):
            raise CoinbaseReadonlyError("Coinbase /accounts returned non-list payload", kind=CoinbaseReadonlyErrorKind.MALFORMED_RESPONSE)
        accounts = []
        for account in raw:
            balance = float(account.get("balance", 0.0) or 0.0)
            available = float(account.get("available", 0.0) or 0.0)
            hold = float(account.get("hold", 0.0) or 0.0)
            if balance == 0 and available == 0 and hold == 0:
                continue
            accounts.append({"id": account.get("id"), "currency": account.get("currency"), "balance": balance, "available": available, "hold": hold, "profile_id": account.get("profile_id")})
        return accounts

    async def get_orders(self, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        raw = await self._get_private(f"/orders?status={status}")
        if not isinstance(raw, list):
            raise CoinbaseReadonlyError("Coinbase /orders returned non-list payload", kind=CoinbaseReadonlyErrorKind.MALFORMED_RESPONSE)
        normalized = []
        for order in raw[:limit]:
            normalized.append({"id": order.get("id"), "product_id": order.get("product_id"), "side": order.get("side"), "type": order.get("type"), "status": order.get("status"), "price": float(order.get("price", 0.0) or 0.0), "size": float(order.get("size", 0.0) or 0.0), "filled_size": float(order.get("filled_size", 0.0) or 0.0), "executed_value": float(order.get("executed_value", 0.0) or 0.0), "fill_fees": float(order.get("fill_fees", 0.0) or 0.0), "created_at": order.get("created_at")})
        return normalized

    async def get_fills(self, product_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        path = "/fills"
        if product_id:
            path += f"?product_id={product_id}"
        raw = await self._get_private(path)
        if not isinstance(raw, list):
            raise CoinbaseReadonlyError("Coinbase /fills returned non-list payload", kind=CoinbaseReadonlyErrorKind.MALFORMED_RESPONSE)
        normalized = []
        for fill in raw[:limit]:
            normalized.append({"trade_id": fill.get("trade_id"), "order_id": fill.get("order_id"), "product_id": fill.get("product_id"), "side": fill.get("side"), "price": float(fill.get("price", 0.0) or 0.0), "size": float(fill.get("size", 0.0) or 0.0), "fee": float(fill.get("fee", 0.0) or 0.0), "liquidity": fill.get("liquidity"), "created_at": fill.get("created_at")})
        return normalized

    async def snapshot(self, symbols: list[str] | None = None) -> dict[str, Any]:
        symbols = symbols or ["BTC-USD", "ETH-USD"]
        started_at = self.utc_now()
        tickers = {}
        for symbol in symbols:
            tickers[symbol] = await self.get_product_ticker(symbol)
        accounts = await self.get_accounts()
        completed_at = self.utc_now()
        return {
            "mode": "live-readonly",
            "orders_allowed": False,
            "live_execution_enabled": False,
            "credentials_configured": self.credentials_configured,
            "credential_alias": self.credential_alias(),
            "exchange": "coinbase_exchange",
            "adapter_version": "coinbase_readonly_v2_phase3",
            "request_started_at": started_at,
            "response_completed_at": completed_at,
            "accounts": accounts,
            "tickers": tickers,
            "timestamp": completed_at,
        }

    async def place_market_buy(self, *args, **kwargs):
        raise CoinbaseReadonlyError("CoinbaseReadonlyAdapterV2 does not place orders")

    async def place_market_sell(self, *args, **kwargs):
        raise CoinbaseReadonlyError("CoinbaseReadonlyAdapterV2 does not place orders")
