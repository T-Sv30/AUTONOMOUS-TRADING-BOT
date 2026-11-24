import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from services.settings_v2 import SettingsV2, TradingMode

SENSITIVE_NAME_PATTERNS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSPHRASE",
    "PRIVATE",
    "API_KEY",
    "ACCESS_KEY",
    "AUTH",
)

DANGEROUS_SECRET_VALUES = {
    "",
    "secret",
    "password",
    "changeme",
    "change-me",
    "replace-me",
    "test",
    "demo",
    "local-debug-placeholder",
}


@dataclass(frozen=True)
class HardeningCheck:
    name: str
    passed: bool
    severity: str
    detail: str
    observed: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
        }
        if self.observed is not None:
            payload["observed"] = self.observed
        return payload


class SecretHardeningServiceV2:
    """Credential and secret posture checks for live-trading readiness.

    This service does not fetch external permissions from Coinbase. It validates
    local configuration invariants and produces log-safe diagnostics. Exchange
    API-key permission review remains an operator runbook step because Coinbase
    key scopes must be verified in the Coinbase UI/account controls.
    """

    @staticmethod
    def is_sensitive_name(name: str) -> bool:
        upper = str(name or "").upper()
        return any(pattern in upper for pattern in SENSITIVE_NAME_PATTERNS)

    @staticmethod
    def fingerprint(value: str) -> str:
        import hashlib

        if not value:
            return ""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]

    @classmethod
    def redact_value(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value)
        if not text:
            return ""
        if len(text) <= 4:
            return "***"
        return f"{text[:2]}***{text[-2:]}"

    @classmethod
    def redact_mapping(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in values.items():
            if cls.is_sensitive_name(key):
                redacted[key] = {
                    "configured": bool(value),
                    "length": len(str(value or "")),
                    "fingerprint": cls.fingerprint(str(value or "")),
                    "redacted": cls.redact_value(value),
                }
            else:
                redacted[key] = value
        return redacted

    @classmethod
    def redact_text(cls, text: str, secrets: Iterable[str]) -> str:
        redacted = str(text or "")
        for secret in sorted({str(item) for item in secrets if item}, key=len, reverse=True):
            if len(secret) < 4:
                continue
            redacted = redacted.replace(secret, cls.redact_value(secret))
        redacted = re.sub(r"(?i)(api[_-]?key|secret|token|password|passphrase)\s*[:=]\s*[^\s,'\"]+", r"\1=***", redacted)
        return redacted

    @staticmethod
    def env_present(name: str, env: Mapping[str, str] | None = None) -> bool:
        source = os.environ if env is None else env
        return bool(source.get(name))

    @classmethod
    def credential_report(cls, settings: SettingsV2) -> dict[str, Any]:
        return {
            "coinbase_exchange_url": settings.coinbase_exchange_url,
            "api_key": {
                "configured": bool(settings.coinbase_exchange_api_key),
                "length": len(settings.coinbase_exchange_api_key or ""),
                "fingerprint": cls.fingerprint(settings.coinbase_exchange_api_key),
            },
            "api_secret": {
                "configured": bool(settings.coinbase_exchange_api_secret),
                "length": len(settings.coinbase_exchange_api_secret or ""),
                "fingerprint": cls.fingerprint(settings.coinbase_exchange_api_secret),
            },
            "passphrase": {
                "configured": bool(settings.coinbase_exchange_passphrase),
                "length": len(settings.coinbase_exchange_passphrase or ""),
                "fingerprint": cls.fingerprint(settings.coinbase_exchange_passphrase),
            },
        }

    @classmethod
    def evaluate(cls, settings: SettingsV2 | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        settings = settings or SettingsV2.from_env()
        source = os.environ if env is None else env
        checks: list[HardeningCheck] = []

        coinbase_fields = {
            "COINBASE_EXCHANGE_API_KEY": settings.coinbase_exchange_api_key,
            "COINBASE_EXCHANGE_API_SECRET": settings.coinbase_exchange_api_secret,
            "COINBASE_EXCHANGE_PASSPHRASE": settings.coinbase_exchange_passphrase,
        }
        configured_coinbase = [name for name, value in coinbase_fields.items() if value]
        missing_coinbase = [name for name, value in coinbase_fields.items() if not value]
        coinbase_required = settings.trading_mode in {TradingMode.LIVE_READONLY, TradingMode.LIVE_TRADING}

        checks.append(HardeningCheck(
            name="coinbase_credentials_present_when_live_mode",
            passed=not coinbase_required or not missing_coinbase,
            severity="critical",
            detail="Coinbase key, secret, and passphrase must be configured for live-readonly or live-trading modes.",
            observed={"configured": configured_coinbase, "missing": missing_coinbase, "trading_mode": settings.trading_mode.value},
        ))

        dangerous = [name for name, value in coinbase_fields.items() if str(value or "").strip().lower() in DANGEROUS_SECRET_VALUES]
        checks.append(HardeningCheck(
            name="coinbase_credentials_not_placeholder_values",
            passed=not dangerous,
            severity="critical",
            detail="Coinbase credential variables must not be empty, placeholder, demo, or obvious test values when configured.",
            observed={"dangerous_fields": dangerous},
        ))

        checks.append(HardeningCheck(
            name="jwt_secret_production_strength",
            passed=settings.debug or (bool(settings.jwt_secret) and len(settings.jwt_secret) >= 32 and settings.jwt_secret.strip().lower() not in DANGEROUS_SECRET_VALUES),
            severity="critical",
            detail="JWT_SECRET must be non-placeholder and at least 32 characters when DEBUG=False.",
            observed={"debug": settings.debug, "configured": bool(settings.jwt_secret), "length": len(settings.jwt_secret or "")},
        ))

        checks.append(HardeningCheck(
            name="live_trading_requires_explicit_live_mode",
            passed=not settings.live_trading_enabled or settings.trading_mode == TradingMode.LIVE_TRADING,
            severity="critical",
            detail="LIVE_TRADING_ENABLED may only be true when TRADING_MODE=live-trading.",
            observed={"trading_mode": settings.trading_mode.value, "live_trading_enabled": settings.live_trading_enabled},
        ))

        checks.append(HardeningCheck(
            name="live_execution_adapter_locked",
            passed=settings.trading_mode != TradingMode.LIVE_TRADING or settings.live_execution_adapter == "coinbase_exchange_v2",
            severity="critical",
            detail="LIVE_EXECUTION_ADAPTER must be coinbase_exchange_v2 in live-trading mode.",
            observed={"execution_adapter": settings.live_execution_adapter},
        ))

        checks.append(HardeningCheck(
            name="manual_approval_token_configured_for_live_trading",
            passed=settings.trading_mode != TradingMode.LIVE_TRADING or not settings.live_manual_approval_required or bool(settings.live_approval_token),
            severity="critical",
            detail="LIVE_APPROVAL_TOKEN must be configured when manual approval is required in live-trading mode.",
            observed={"manual_approval_required": settings.live_manual_approval_required, "token_configured": bool(settings.live_approval_token)},
        ))

        checks.append(HardeningCheck(
            name="live_order_notional_cap_is_tiny",
            passed=float(settings.live_max_order_notional_usd) <= 25.0,
            severity="critical",
            detail="Manual live pilot max notional should remain tiny until after repeated reviewed pilots.",
            observed={"live_max_order_notional_usd": settings.live_max_order_notional_usd},
        ))

        checks.append(HardeningCheck(
            name="kill_switch_defaults_safe_when_not_live_trading",
            passed=settings.trading_mode == TradingMode.LIVE_TRADING or settings.coinbase_live_order_kill_switch is True,
            severity="critical",
            detail="COINBASE_LIVE_ORDER_KILL_SWITCH should stay true outside the exact live submit window.",
            observed={"trading_mode": settings.trading_mode.value, "coinbase_live_order_kill_switch": settings.coinbase_live_order_kill_switch},
        ))

        checks.append(HardeningCheck(
            name="wildcard_cors_not_enabled_in_production",
            passed=settings.debug or "*" not in settings.cors_origins,
            severity="critical",
            detail="Wildcard CORS is only acceptable in local debug mode.",
            observed={"debug": settings.debug, "cors_origins": settings.cors_origins},
        ))

        accidental_frontend_secrets = [name for name in source.keys() if str(name).startswith("REACT_APP_") and cls.is_sensitive_name(name)]
        checks.append(HardeningCheck(
            name="no_frontend_prefixed_secrets",
            passed=not accidental_frontend_secrets,
            severity="critical",
            detail="Secrets must never use REACT_APP_ prefixes because frontend build variables are public.",
            observed={"frontend_secret_like_variables": accidental_frontend_secrets},
        ))

        blockers = [check.to_dict() for check in checks if check.severity == "critical" and not check.passed]
        return {
            "status": "hardened" if not blockers else "not_hardened",
            "ready_for_live_credentials": not blockers,
            "blockers": blockers,
            "checks": [check.to_dict() for check in checks],
            "credentials": cls.credential_report(settings),
            "redacted_settings": settings.redacted_report(),
        }
