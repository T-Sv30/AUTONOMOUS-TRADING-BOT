from datetime import UTC, datetime
from typing import Any

from services.settings_v2 import SETTINGS, SettingsV2, TradingMode


class OperationalReadinessServiceV2:
    """Environment and runtime readiness checks for deployment operations."""

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _check(name: str, passed: bool, severity: str, message: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "passed": passed,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
        }

    def validate_environment(self, strict: bool = False, settings: SettingsV2 = SETTINGS) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        checks.append(self._check("jwt_secret_configured", bool(settings.jwt_secret), "critical", "JWT_SECRET must be configured."))
        checks.append(self._check("mongo_url_configured", bool(settings.mongo_url), "critical", "MONGO_URL must be configured."))
        checks.append(self._check("db_name_configured", bool(settings.db_name), "critical", "DB_NAME must be configured."))
        checks.append(self._check("cors_origins_explicit", bool(settings.cors_origins) and "*" not in settings.cors_origins, "critical", "CORS_ORIGINS must be explicit; wildcard is not allowed outside debug."))
        checks.append(self._check("valid_runtime_role", settings.runtime_role.value in {"api", "worker", "all", "indexes"}, "critical", "RUNTIME_ROLE must be api, worker, all, or indexes.", {"runtime_role": settings.runtime_role.value}))
        checks.append(self._check("valid_trading_mode", settings.trading_mode.value in {"paper", "live-readonly", "live-trading"}, "critical", "TRADING_MODE must be paper, live-readonly, or live-trading.", {"trading_mode": settings.trading_mode.value}))
        checks.append(self._check("live_trading_disabled_by_default", not settings.live_trading_enabled or settings.trading_mode == TradingMode.LIVE_TRADING, "critical", "LIVE_TRADING_ENABLED should only be true with TRADING_MODE=live-trading.", {"live_trading_enabled": settings.live_trading_enabled, "trading_mode": settings.trading_mode.value}))

        if settings.trading_mode == TradingMode.PAPER:
            checks.append(self._check("market_data_source_live", settings.market_data_source == "live", "warning", "Paper results only reflect the market when MARKET_DATA_SOURCE=live."))

        if settings.trading_mode == TradingMode.LIVE_READONLY:
            checks.append(self._check("coinbase_readonly_key", bool(settings.coinbase_exchange_api_key), "critical", "Readonly Coinbase API key required."))
            checks.append(self._check("coinbase_readonly_secret", bool(settings.coinbase_exchange_api_secret), "critical", "Readonly Coinbase API secret required."))
            checks.append(self._check("coinbase_readonly_passphrase", bool(settings.coinbase_exchange_passphrase), "critical", "Readonly Coinbase passphrase required."))
            checks.append(self._check("live_execution_off_in_readonly", not settings.live_trading_enabled, "critical", "LIVE_TRADING_ENABLED must be false in live-readonly mode."))

        if settings.trading_mode == TradingMode.LIVE_TRADING:
            checks.append(self._check("live_global_gate_enabled", settings.live_trading_enabled, "critical", "LIVE_TRADING_ENABLED must be true for live-trading mode."))
            checks.append(self._check("live_adapter_selected", settings.live_execution_adapter == "coinbase_exchange_v2", "critical", "LIVE_EXECUTION_ADAPTER must be coinbase_exchange_v2."))
            checks.append(self._check("live_approval_token_configured", bool(settings.live_approval_token), "critical", "LIVE_APPROVAL_TOKEN must be configured for non-dry-run live orders."))
            checks.append(self._check("live_max_notional_low", settings.live_max_order_notional_usd <= 25.0, "warning", "Initial live max order notional should be <= 25 USD."))
            checks.append(self._check("coinbase_live_key", bool(settings.coinbase_exchange_api_key), "critical", "Coinbase API key required."))
            checks.append(self._check("coinbase_live_secret", bool(settings.coinbase_exchange_api_secret), "critical", "Coinbase API secret required."))
            checks.append(self._check("coinbase_live_passphrase", bool(settings.coinbase_exchange_passphrase), "critical", "Coinbase passphrase required."))
            checks.append(self._check("live_order_kill_switch_default", settings.coinbase_live_order_kill_switch, "warning", "COINBASE_LIVE_ORDER_KILL_SWITCH should default to true outside approved execution windows."))

        if not settings.debug:
            checks.append(self._check("debug_disabled", not settings.debug, "critical", "DEBUG must be false in production."))

        failed_critical = [check for check in checks if not check["passed"] and check["severity"] == "critical"]
        failed_warning = [check for check in checks if not check["passed"] and check["severity"] == "warning"]
        status = "ready" if not failed_critical and not failed_warning else "degraded" if not failed_critical else "blocked"
        if strict and failed_warning and not failed_critical:
            status = "blocked"
        return {
            "status": status,
            "strict": strict,
            "settings": settings.redacted_report(),
            "checks": checks,
            "summary": {
                "total": len(checks),
                "failed_critical": len(failed_critical),
                "failed_warning": len(failed_warning),
            },
            "generated_at": self.utc_now(),
        }

    async def database_health(self, db) -> dict[str, Any]:
        try:
            await db.command("ping")
            return {"status": "ready", "database": db.name, "checked_at": self.utc_now()}
        except Exception as exc:
            return {"status": "blocked", "error": str(exc), "checked_at": self.utc_now()}

    async def readiness(self, db, strict: bool = False) -> dict[str, Any]:
        environment = self.validate_environment(strict=strict)
        database = await self.database_health(db)
        status = "ready" if environment["status"] == "ready" and database["status"] == "ready" else "degraded" if database["status"] == "ready" else "blocked"
        return {
            "status": status,
            "environment": environment,
            "database": database,
            "checked_at": self.utc_now(),
        }
