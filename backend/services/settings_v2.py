import logging
import os
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class RuntimeRole(str, Enum):
    API = "api"
    WORKER = "worker"
    ALL = "all"
    INDEXES = "indexes"


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE_READONLY = "live-readonly"
    LIVE_TRADING = "live-trading"


class SettingsV2(BaseModel):
    """Typed, validated runtime settings.

    This class deliberately avoids logging raw secrets. Use `redacted_report()`
    for diagnostics/readiness output.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    debug: bool = False
    simulation_mode: bool = True
    trading_mode: TradingMode = TradingMode.PAPER
    runtime_role: RuntimeRole = RuntimeRole.API
    run_mongo_index_bootstrap: bool = False
    api_embed_bot_manager: bool = False

    jwt_secret: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    mongo_url: str = "mongodb://localhost:27017"
    db_name: str = "trading_bot"

    ops_admin_enabled: bool = False
    ops_admin_emails: set[str] = Field(default_factory=set)

    coinbase_exchange_url: str = "https://api.exchange.coinbase.com"
    coinbase_exchange_api_key: str = ""
    coinbase_exchange_api_secret: str = ""
    coinbase_exchange_passphrase: str = ""

    live_trading_enabled: bool = False
    live_execution_adapter: str = "disabled"
    live_allowed_symbols: list[str] = Field(default_factory=lambda: ["BTC-USD", "ETH-USD"])
    live_max_order_notional_usd: float = 25.0
    live_manual_approval_required: bool = True
    live_approval_token: str = ""
    coinbase_live_order_kill_switch: bool = True

    @staticmethod
    def env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def parse_csv(value: str) -> list[str]:
        return [item.strip() for item in str(value or "").split(",") if item.strip()]

    @classmethod
    def from_env(cls) -> "SettingsV2":
        debug = cls.env_bool("DEBUG", False)
        raw_role = os.getenv("RUNTIME_ROLE", "all" if debug else "api")
        runtime_role = RuntimeRole(str(raw_role).strip().lower())
        return cls(
            debug=debug,
            simulation_mode=cls.env_bool("SIMULATION_MODE", True),
            trading_mode=TradingMode(os.getenv("TRADING_MODE", "paper")),
            runtime_role=runtime_role,
            run_mongo_index_bootstrap=cls.env_bool("RUN_MONGO_INDEX_BOOTSTRAP", debug),
            api_embed_bot_manager=cls.env_bool("API_EMBED_BOT_MANAGER", debug or runtime_role == RuntimeRole.ALL),
            jwt_secret=os.getenv("JWT_SECRET") or ("local-debug-placeholder" if debug else ""),
            cors_origins=cls.parse_csv(os.getenv("CORS_ORIGINS", "http://localhost:3000")),
            mongo_url=os.getenv("MONGO_URL", "mongodb://localhost:27017"),
            db_name=os.getenv("DB_NAME", "trading_bot"),
            ops_admin_enabled=cls.env_bool("OPS_ADMIN_ENABLED", False),
            ops_admin_emails={email.lower() for email in cls.parse_csv(os.getenv("OPS_ADMIN_EMAILS", ""))},
            coinbase_exchange_url=os.getenv("COINBASE_EXCHANGE_URL", "https://api.exchange.coinbase.com"),
            coinbase_exchange_api_key=os.getenv("COINBASE_EXCHANGE_API_KEY", ""),
            coinbase_exchange_api_secret=os.getenv("COINBASE_EXCHANGE_API_SECRET", ""),
            coinbase_exchange_passphrase=os.getenv("COINBASE_EXCHANGE_PASSPHRASE", ""),
            live_trading_enabled=cls.env_bool("LIVE_TRADING_ENABLED", False),
            live_execution_adapter=os.getenv("LIVE_EXECUTION_ADAPTER", "disabled"),
            live_allowed_symbols=cls.parse_csv(os.getenv("LIVE_ALLOWED_SYMBOLS", "BTC-USD,ETH-USD")) or ["BTC-USD", "ETH-USD"],
            live_max_order_notional_usd=float(os.getenv("LIVE_MAX_ORDER_NOTIONAL_USD", "25") or 25),
            live_manual_approval_required=cls.env_bool("LIVE_MANUAL_APPROVAL_REQUIRED", True),
            live_approval_token=os.getenv("LIVE_APPROVAL_TOKEN", ""),
            coinbase_live_order_kill_switch=cls.env_bool("COINBASE_LIVE_ORDER_KILL_SWITCH", True),
        )

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str], info):
        debug = bool(info.data.get("debug", False))
        if not origins:
            raise ValueError("CORS_ORIGINS must include at least one explicit origin")
        if "*" in origins and not debug:
            raise ValueError("Wildcard CORS is only allowed when DEBUG=True")
        for origin in origins:
            if origin == "*" and debug:
                continue
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Invalid CORS origin {origin!r}; expected absolute http(s) origin")
            if not debug and origin.startswith("http://") and "localhost" not in origin and "127.0.0.1" not in origin:
                raise ValueError("Production CORS origins must use https unless localhost-only")
        return origins

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, secret: str, info):
        debug = bool(info.data.get("debug", False))
        if debug:
            return secret
        if not secret:
            raise ValueError("JWT_SECRET must be configured unless DEBUG=True")
        if len(secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters when DEBUG=False")
        weak_values = {"secret", "password", "changeme", "replace-me", "local-debug-placeholder"}
        if secret.strip().lower() in weak_values:
            raise ValueError("JWT_SECRET is too weak for production use")
        return secret

    @model_validator(mode="after")
    def validate_live_invariants(self) -> "SettingsV2":
        if self.live_trading_enabled and self.trading_mode != TradingMode.LIVE_TRADING:
            raise ValueError("LIVE_TRADING_ENABLED may only be true when TRADING_MODE=live-trading")
        if self.trading_mode == TradingMode.LIVE_READONLY and self.live_trading_enabled:
            raise ValueError("LIVE_TRADING_ENABLED must be false in live-readonly mode")
        if self.trading_mode == TradingMode.LIVE_TRADING:
            if self.live_execution_adapter != "coinbase_exchange_v2":
                raise ValueError("LIVE_EXECUTION_ADAPTER must be coinbase_exchange_v2 in live-trading mode")
            if self.live_manual_approval_required and not self.live_approval_token:
                raise ValueError("LIVE_APPROVAL_TOKEN must be configured when manual live approval is required")
        return self

    @staticmethod
    def secret_report(value: str) -> dict[str, Any]:
        return {
            "configured": bool(value),
            "length": len(value or ""),
            "redacted": "***" if value else "",
        }

    def redacted_report(self) -> dict[str, Any]:
        return {
            "debug": self.debug,
            "simulation_mode": self.simulation_mode,
            "trading_mode": self.trading_mode.value,
            "runtime_role": self.runtime_role.value,
            "run_mongo_index_bootstrap": self.run_mongo_index_bootstrap,
            "api_embed_bot_manager": self.api_embed_bot_manager,
            "cors_origins": self.cors_origins,
            "mongo": {"url_configured": bool(self.mongo_url), "db_name": self.db_name},
            "jwt_secret": self.secret_report(self.jwt_secret),
            "ops_admin": {"enabled": self.ops_admin_enabled, "email_count": len(self.ops_admin_emails)},
            "coinbase": {
                "exchange_url": self.coinbase_exchange_url,
                "api_key": self.secret_report(self.coinbase_exchange_api_key),
                "api_secret": self.secret_report(self.coinbase_exchange_api_secret),
                "passphrase": self.secret_report(self.coinbase_exchange_passphrase),
            },
            "live_trading": {
                "enabled": self.live_trading_enabled,
                "execution_adapter": self.live_execution_adapter,
                "allowed_symbols": self.live_allowed_symbols,
                "max_order_notional_usd": self.live_max_order_notional_usd,
                "manual_approval_required": self.live_manual_approval_required,
                "approval_token": self.secret_report(self.live_approval_token),
                "coinbase_live_order_kill_switch": self.coinbase_live_order_kill_switch,
            },
        }


SETTINGS = SettingsV2.from_env()
if SETTINGS.debug and SETTINGS.jwt_secret == "local-debug-placeholder":  # noqa: S105 - detects the placeholder, not a secret  # nosec B105
    logger.warning("Using local DEBUG JWT placeholder. Set JWT_SECRET before deployment.")
