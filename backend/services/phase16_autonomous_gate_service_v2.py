from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class AutonomousGateCheck:
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


class Phase16AutonomousGateServiceV2:
    """Design gate for any future autonomous live mode.

    This service does not submit orders and does not enable autonomous execution.
    It defines the minimum evidence and controls required before later phases may
    even consider shadow-mode or canary autonomous operation.
    """

    DEFAULT_ALLOWED_STRATEGIES = {"ma_cross_risk_managed_v1"}
    DEFAULT_ALLOWED_SYMBOLS = {"BTC-USD", "ETH-USD"}
    DEFAULT_MAX_ORDER_NOTIONAL_USD = 5.0
    DEFAULT_MAX_DAILY_NOTIONAL_USD = 25.0
    DEFAULT_MAX_DAILY_LOSS_USD = 10.0
    DEFAULT_MAX_DRAWDOWN_PCT = 2.0
    DEFAULT_MAX_OPEN_POSITIONS = 1
    DEFAULT_MIN_BACKTEST_TRADES = 30
    DEFAULT_MIN_WALK_FORWARD_WINDOWS = 3
    DEFAULT_MIN_SHADOW_DAYS_REQUIRED = 14
    REQUIRED_MODE = "autonomous_live_candidate"

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_16_autonomous_live_gate_design",
            "release_scope": "design_only_no_execution",
            "autonomous_execution_enabled": False,
            "required_mode": cls.REQUIRED_MODE,
            "allowed_strategies": sorted(cls.DEFAULT_ALLOWED_STRATEGIES),
            "allowed_symbols": sorted(cls.DEFAULT_ALLOWED_SYMBOLS),
            "max_order_notional_usd": cls.DEFAULT_MAX_ORDER_NOTIONAL_USD,
            "max_daily_notional_usd": cls.DEFAULT_MAX_DAILY_NOTIONAL_USD,
            "max_daily_loss_usd": cls.DEFAULT_MAX_DAILY_LOSS_USD,
            "max_drawdown_pct": cls.DEFAULT_MAX_DRAWDOWN_PCT,
            "max_open_positions": cls.DEFAULT_MAX_OPEN_POSITIONS,
            "min_backtest_trades": cls.DEFAULT_MIN_BACKTEST_TRADES,
            "min_walk_forward_windows": cls.DEFAULT_MIN_WALK_FORWARD_WINDOWS,
            "min_shadow_days_required": cls.DEFAULT_MIN_SHADOW_DAYS_REQUIRED,
            "required_controls": [
                "explicit_candidate_mode",
                "strategy_allowlist",
                "symbol_allowlist",
                "per_strategy_operator_approval",
                "autonomous_execution_disabled_until_future_phase",
                "manual_override_enabled",
                "emergency_halt_enabled",
                "market_data_freshness_gate",
                "exchange_connectivity_gate",
                "risk_limits",
                "backtest_evidence",
                "walk_forward_evidence",
                "shadow_mode_required_before_canary",
                "cooldown_after_loss",
                "cooldown_after_exchange_error",
                "post_order_reconciliation_required",
                "audit_chain_required",
            ],
        }

    @staticmethod
    def bool_value(values: Mapping[str, Any], key: str) -> bool:
        value = values.get(key)
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}

    @classmethod
    def evaluate_design(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        strategy = str(config.get("strategy", "")).strip()
        symbols = {str(symbol).upper() for symbol in (config.get("symbols") or [])}
        unknown_symbols = sorted(symbols - cls.DEFAULT_ALLOWED_SYMBOLS)

        def as_float(name: str) -> float:
            try:
                return float(config.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        def as_int(name: str) -> int:
            try:
                return int(config.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0

        max_order_notional = as_float("max_order_notional_usd")
        max_daily_notional = as_float("max_daily_notional_usd")
        max_daily_loss = as_float("max_daily_loss_usd")
        max_drawdown = as_float("max_drawdown_pct")
        max_open_positions = as_int("max_open_positions")
        min_signal_confidence = as_float("min_signal_confidence")
        backtest_trades = as_int("backtest_trades")
        walk_forward_windows = as_int("walk_forward_windows")
        shadow_days_completed = as_int("shadow_days_completed")
        market_data_max_age_seconds = as_int("market_data_max_age_seconds")
        reconciliation_interval_minutes = as_int("reconciliation_interval_minutes")
        cooldown_after_loss_minutes = as_int("cooldown_after_loss_minutes")
        cooldown_after_error_minutes = as_int("cooldown_after_error_minutes")

        checks: list[AutonomousGateCheck] = [
            AutonomousGateCheck(
                name="candidate_mode_explicit",
                passed=config.get("mode") == cls.REQUIRED_MODE,
                severity="critical",
                detail="Autonomous gate design requires an explicit candidate mode distinct from manual modes.",
                observed={"mode": config.get("mode"), "required_mode": cls.REQUIRED_MODE},
            ),
            AutonomousGateCheck(
                name="autonomous_execution_disabled_in_design_phase",
                passed=not cls.bool_value(config, "autonomous_execution_enabled"),
                severity="critical",
                detail="Phase 16 is design-only; autonomous execution must remain disabled.",
                observed={"autonomous_execution_enabled": config.get("autonomous_execution_enabled")},
            ),
            AutonomousGateCheck(
                name="strategy_is_allowlisted",
                passed=strategy in cls.DEFAULT_ALLOWED_STRATEGIES,
                severity="critical",
                detail="Only explicitly allowlisted strategies may be considered for future autonomous phases.",
                observed={"strategy": strategy, "allowed_strategies": sorted(cls.DEFAULT_ALLOWED_STRATEGIES)},
            ),
            AutonomousGateCheck(
                name="symbols_are_allowlisted",
                passed=bool(symbols) and not unknown_symbols,
                severity="critical",
                detail="Autonomous candidate symbols must stay inside the approved spot allowlist.",
                observed={"symbols": sorted(symbols), "unknown_symbols": unknown_symbols},
            ),
            AutonomousGateCheck(
                name="strategy_operator_approved",
                passed=cls.bool_value(config, "strategy_operator_approved"),
                severity="critical",
                detail="The specific strategy must have explicit operator approval before future autonomous evaluation.",
                observed={"strategy_operator_approved": config.get("strategy_operator_approved")},
            ),
            AutonomousGateCheck(
                name="manual_override_and_emergency_halt_enabled",
                passed=cls.bool_value(config, "manual_override_enabled") and cls.bool_value(config, "emergency_halt_enabled"),
                severity="critical",
                detail="Manual override and emergency halt controls are mandatory for future autonomous operation.",
                observed={
                    "manual_override_enabled": config.get("manual_override_enabled"),
                    "emergency_halt_enabled": config.get("emergency_halt_enabled"),
                },
            ),
            AutonomousGateCheck(
                name="market_data_freshness_gate_defined",
                passed=0 < market_data_max_age_seconds <= 30,
                severity="critical",
                detail="Market data freshness gate must be defined and strict.",
                observed={"market_data_max_age_seconds": market_data_max_age_seconds},
            ),
            AutonomousGateCheck(
                name="exchange_connectivity_gate_defined",
                passed=cls.bool_value(config, "exchange_connectivity_gate_enabled"),
                severity="critical",
                detail="Exchange connectivity gate must halt future autonomous decisions when connectivity is degraded.",
                observed={"exchange_connectivity_gate_enabled": config.get("exchange_connectivity_gate_enabled")},
            ),
            AutonomousGateCheck(
                name="risk_limits_are_tiny_and_explicit",
                passed=(
                    0.0 < max_order_notional <= cls.DEFAULT_MAX_ORDER_NOTIONAL_USD
                    and 0.0 < max_daily_notional <= cls.DEFAULT_MAX_DAILY_NOTIONAL_USD
                    and 0.0 < max_daily_loss <= cls.DEFAULT_MAX_DAILY_LOSS_USD
                    and 0.0 < max_drawdown <= cls.DEFAULT_MAX_DRAWDOWN_PCT
                    and 0 < max_open_positions <= cls.DEFAULT_MAX_OPEN_POSITIONS
                ),
                severity="critical",
                detail="Autonomous candidate risk limits must remain tiny and explicit.",
                observed={
                    "max_order_notional_usd": max_order_notional,
                    "max_daily_notional_usd": max_daily_notional,
                    "max_daily_loss_usd": max_daily_loss,
                    "max_drawdown_pct": max_drawdown,
                    "max_open_positions": max_open_positions,
                },
            ),
            AutonomousGateCheck(
                name="strategy_confidence_threshold_defined",
                passed=0.5 <= min_signal_confidence <= 1.0,
                severity="critical",
                detail="A bounded strategy confidence threshold must be defined before autonomous evaluation.",
                observed={"min_signal_confidence": min_signal_confidence},
            ),
            AutonomousGateCheck(
                name="backtest_evidence_sufficient",
                passed=cls.bool_value(config, "backtest_passed") and backtest_trades >= cls.DEFAULT_MIN_BACKTEST_TRADES,
                severity="critical",
                detail="Backtest evidence must pass and include enough trades to be meaningful.",
                observed={"backtest_passed": config.get("backtest_passed"), "backtest_trades": backtest_trades},
            ),
            AutonomousGateCheck(
                name="walk_forward_evidence_sufficient",
                passed=cls.bool_value(config, "walk_forward_passed") and walk_forward_windows >= cls.DEFAULT_MIN_WALK_FORWARD_WINDOWS,
                severity="critical",
                detail="Walk-forward validation must pass across enough windows.",
                observed={"walk_forward_passed": config.get("walk_forward_passed"), "walk_forward_windows": walk_forward_windows},
            ),
            AutonomousGateCheck(
                name="shadow_mode_required_before_canary",
                passed=shadow_days_completed >= cls.DEFAULT_MIN_SHADOW_DAYS_REQUIRED and cls.bool_value(config, "shadow_mode_passed"),
                severity="critical",
                detail="Shadow mode must be completed before autonomous canary can be considered.",
                observed={"shadow_days_completed": shadow_days_completed, "shadow_mode_passed": config.get("shadow_mode_passed")},
            ),
            AutonomousGateCheck(
                name="cooldowns_defined",
                passed=cooldown_after_loss_minutes >= 1440 and cooldown_after_error_minutes >= 60,
                severity="critical",
                detail="Cooldowns must be defined after losses and exchange errors.",
                observed={
                    "cooldown_after_loss_minutes": cooldown_after_loss_minutes,
                    "cooldown_after_error_minutes": cooldown_after_error_minutes,
                },
            ),
            AutonomousGateCheck(
                name="reconciliation_and_audit_required",
                passed=(
                    cls.bool_value(config, "post_order_reconciliation_required")
                    and reconciliation_interval_minutes > 0
                    and reconciliation_interval_minutes <= 15
                    and cls.bool_value(config, "audit_chain_required")
                ),
                severity="critical",
                detail="Future autonomous operation requires frequent reconciliation and tamper-evident audit chain.",
                observed={
                    "post_order_reconciliation_required": config.get("post_order_reconciliation_required"),
                    "reconciliation_interval_minutes": reconciliation_interval_minutes,
                    "audit_chain_required": config.get("audit_chain_required"),
                },
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "design_gate_satisfied" if not blockers else "blocked",
            "ready_for_autonomous_shadow_mode_design_review": not blockers,
            "autonomous_execution_enabled": False,
            "blockers": blockers,
            "checks": serialized,
            "policy": cls.policy(),
            "evaluated_at": cls.utc_now(),
        }

    async def evaluate(self, user_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        result = self.evaluate_design(config)
        await self.db.autonomous_gate_design_reviews.insert_one({
            "user_id": user_id,
            "config": dict(config),
            "result": result,
            "created_at": self.utc_now(),
        })
        return result
