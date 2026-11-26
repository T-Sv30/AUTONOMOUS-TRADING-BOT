import hmac
import os
from dataclasses import asdict, dataclass
from typing import Any


class LiveTradingGateError(RuntimeError):
    pass


@dataclass
class LiveTradingGateConfig:
    live_trading_enabled: bool = False
    execution_adapter: str = "disabled"
    max_order_notional_usd: float = 25.0
    allowed_symbols: tuple[str, ...] = ("BTC-USD", "ETH-USD")
    manual_approval_required: bool = True
    signed_approval_required: bool = True
    approval_token: str | None = None


class LiveTradingGateV2:
    """Fail-closed gate for all live-order paths."""

    REQUIRED_ADAPTER = "coinbase_exchange_v2"

    def __init__(self, config: LiveTradingGateConfig | None = None):
        self.config = config or self.from_env()

    @staticmethod
    def env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def approval_tokens_match(provided: str | None, expected: str | None) -> bool:
        if not provided or not expected:
            return False
        return hmac.compare_digest(str(provided), str(expected))

    @classmethod
    def from_env(cls) -> LiveTradingGateConfig:
        symbols = tuple(
            symbol.strip()
            for symbol in os.getenv("LIVE_ALLOWED_SYMBOLS", "BTC-USD,ETH-USD").split(",")
            if symbol.strip()
        )
        try:
            max_notional = float(os.getenv("LIVE_MAX_ORDER_NOTIONAL_USD", "25"))
        except ValueError:
            max_notional = 25.0
        return LiveTradingGateConfig(
            live_trading_enabled=cls.env_bool("LIVE_TRADING_ENABLED", False),
            execution_adapter=os.getenv("LIVE_EXECUTION_ADAPTER", "disabled"),
            max_order_notional_usd=max_notional,
            allowed_symbols=symbols or ("BTC-USD", "ETH-USD"),
            manual_approval_required=cls.env_bool("LIVE_MANUAL_APPROVAL_REQUIRED", True),
            signed_approval_required=cls.env_bool("LIVE_SIGNED_APPROVAL_REQUIRED", True),
            approval_token=os.getenv("LIVE_APPROVAL_TOKEN"),
        )

    def describe(self) -> dict[str, Any]:
        payload = asdict(self.config)
        payload["approval_token_configured"] = bool(self.config.approval_token)
        payload.pop("approval_token", None)
        payload["required_adapter"] = self.REQUIRED_ADAPTER
        payload["orders_require_live_trading_mode"] = True
        return payload

    def preflight(
        self,
        *,
        trading_mode: str,
        user_config: dict[str, Any] | None,
        symbol: str,
        side: str,
        notional_usd: float,
        approval_token: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        user_config = user_config or {}
        checks = []

        def check(name: str, passed: bool, reason: str):
            checks.append({"name": name, "passed": passed, "reason": reason})
            if not passed:
                raise LiveTradingGateError(reason)

        check("trading_mode", trading_mode == "live-trading", "TRADING_MODE must be live-trading for live orders")
        check("global_live_gate", self.config.live_trading_enabled, "LIVE_TRADING_ENABLED must be true")
        check("adapter_gate", self.config.execution_adapter == self.REQUIRED_ADAPTER, f"LIVE_EXECUTION_ADAPTER must be {self.REQUIRED_ADAPTER}")
        check("user_live_gate", bool(user_config.get("live_trading_enabled")), "user bot config must set live_trading_enabled=true")
        check("symbol_allowlist", symbol in self.config.allowed_symbols, "symbol is not in LIVE_ALLOWED_SYMBOLS")
        check("positive_notional", float(notional_usd) > 0, "order notional must be positive")
        check("max_notional", float(notional_usd) <= self.config.max_order_notional_usd, "order exceeds LIVE_MAX_ORDER_NOTIONAL_USD")

        if self.config.manual_approval_required and not dry_run:
            if self.config.signed_approval_required:
                check("signed_approval_token_present", bool(approval_token), "signed live approval challenge token required")
            else:
                check("approval_token_configured", bool(self.config.approval_token), "LIVE_APPROVAL_TOKEN must be configured when manual approval is required")
                check("approval_token", self.approval_tokens_match(approval_token, self.config.approval_token), "valid live approval token required")

        return {
            "allowed": True,
            "dry_run": dry_run,
            "symbol": symbol,
            "side": side.upper(),
            "notional_usd": round(float(notional_usd), 8),
            "checks": checks,
            "gate": self.describe(),
        }

    def safe_preflight(self, **kwargs) -> dict[str, Any]:
        try:
            return self.preflight(**kwargs)
        except LiveTradingGateError as exc:
            return {
                "allowed": False,
                "dry_run": bool(kwargs.get("dry_run", False)),
                "symbol": kwargs.get("symbol"),
                "side": str(kwargs.get("side", "")).upper(),
                "notional_usd": round(float(kwargs.get("notional_usd", 0.0) or 0.0), 8),
                "reason": str(exc),
                "gate": self.describe(),
            }
