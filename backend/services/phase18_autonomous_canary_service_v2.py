from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class CanaryCheck:
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


class Phase18AutonomousCanaryServiceV2:
    """Control gate for a future tiny autonomous canary.

    This service does not submit orders. It evaluates whether the system is
    eligible to consider one tightly bounded autonomous canary attempt after
    shadow mode has passed review.
    """

    ALLOWED_STRATEGIES = {"ma_cross_risk_managed_v1"}
    ALLOWED_SYMBOLS = {"BTC-USD"}
    REQUIRED_MODE = "autonomous_canary_candidate"
    DEFAULT_MAX_ORDER_NOTIONAL_USD = 2.0
    DEFAULT_MAX_DAILY_NOTIONAL_USD = 2.0
    DEFAULT_MAX_DAILY_LOSS_USD = 2.0
    DEFAULT_MAX_ORDERS_PER_DAY = 1
    DEFAULT_MAX_OPEN_ORDERS = 1

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_18_autonomous_live_canary_controls",
            "release_scope": "single_tiny_autonomous_canary_candidate",
            "live_order_submission_by_this_service": "not_allowed",
            "allowed_strategies": sorted(cls.ALLOWED_STRATEGIES),
            "allowed_symbols": sorted(cls.ALLOWED_SYMBOLS),
            "required_mode": cls.REQUIRED_MODE,
            "max_order_notional_usd": cls.DEFAULT_MAX_ORDER_NOTIONAL_USD,
            "max_daily_notional_usd": cls.DEFAULT_MAX_DAILY_NOTIONAL_USD,
            "max_daily_loss_usd": cls.DEFAULT_MAX_DAILY_LOSS_USD,
            "max_orders_per_day": cls.DEFAULT_MAX_ORDERS_PER_DAY,
            "max_open_orders": cls.DEFAULT_MAX_OPEN_ORDERS,
            "required_controls": [
                "phase16_design_gate_satisfied",
                "phase17_shadow_review_passed",
                "single_strategy",
                "single_symbol",
                "tiny_notional",
                "one_order_per_day",
                "operator_canary_approval",
                "autonomous_canary_mode_explicit",
                "global_kill_switch_available",
                "auto_halt_after_any_anomaly",
                "post_order_reconciliation_required",
                "operator_alert_required",
                "canary_report_required",
                "canary_signoff_required",
                "scale_up_blocked_until_review",
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
    def evaluate_candidate(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        strategy = str(config.get("strategy", "")).strip()
        symbols = {str(symbol).upper() for symbol in (config.get("symbols") or [])}
        unknown_symbols = sorted(symbols - cls.ALLOWED_SYMBOLS)

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
        max_orders_per_day = as_int("max_orders_per_day")
        open_orders = as_int("open_orders")
        pending_reconciliation_count = as_int("pending_reconciliation_count")
        unresolved_canary_report_count = as_int("unresolved_canary_report_count")
        active_halt_count = as_int("active_halt_count")

        checks: list[CanaryCheck] = [
            CanaryCheck(
                name="phase16_design_gate_satisfied",
                passed=cls.bool_value(config, "phase16_design_gate_satisfied"),
                severity="critical",
                detail="Phase 16 autonomous gate design must be satisfied before canary consideration.",
                observed={"phase16_design_gate_satisfied": config.get("phase16_design_gate_satisfied")},
            ),
            CanaryCheck(
                name="phase17_shadow_review_passed",
                passed=cls.bool_value(config, "phase17_shadow_review_passed"),
                severity="critical",
                detail="Phase 17 shadow-mode review must pass before canary consideration.",
                observed={"phase17_shadow_review_passed": config.get("phase17_shadow_review_passed")},
            ),
            CanaryCheck(
                name="canary_mode_explicit",
                passed=config.get("mode") == cls.REQUIRED_MODE,
                severity="critical",
                detail="Canary consideration requires an explicit canary candidate mode.",
                observed={"mode": config.get("mode"), "required_mode": cls.REQUIRED_MODE},
            ),
            CanaryCheck(
                name="strategy_is_single_and_allowlisted",
                passed=strategy in cls.ALLOWED_STRATEGIES,
                severity="critical",
                detail="Canary is limited to one allowlisted strategy.",
                observed={"strategy": strategy, "allowed_strategies": sorted(cls.ALLOWED_STRATEGIES)},
            ),
            CanaryCheck(
                name="symbol_is_single_and_allowlisted",
                passed=len(symbols) == 1 and not unknown_symbols,
                severity="critical",
                detail="Canary is limited to one allowlisted symbol.",
                observed={"symbols": sorted(symbols), "unknown_symbols": unknown_symbols, "allowed_symbols": sorted(cls.ALLOWED_SYMBOLS)},
            ),
            CanaryCheck(
                name="operator_canary_approval_present",
                passed=cls.bool_value(config, "operator_canary_approval"),
                severity="critical",
                detail="A human operator must approve the canary window before it is considered.",
                observed={"operator_canary_approval": config.get("operator_canary_approval")},
            ),
            CanaryCheck(
                name="tiny_notional_caps_enforced",
                passed=(
                    0.0 < max_order_notional <= cls.DEFAULT_MAX_ORDER_NOTIONAL_USD
                    and 0.0 < max_daily_notional <= cls.DEFAULT_MAX_DAILY_NOTIONAL_USD
                    and 0.0 < max_daily_loss <= cls.DEFAULT_MAX_DAILY_LOSS_USD
                ),
                severity="critical",
                detail="Canary notional and loss caps must remain tiny.",
                observed={
                    "max_order_notional_usd": max_order_notional,
                    "max_daily_notional_usd": max_daily_notional,
                    "max_daily_loss_usd": max_daily_loss,
                },
            ),
            CanaryCheck(
                name="one_order_limit_enforced",
                passed=max_orders_per_day == cls.DEFAULT_MAX_ORDERS_PER_DAY and open_orders <= cls.DEFAULT_MAX_OPEN_ORDERS,
                severity="critical",
                detail="Canary is limited to one order per day and one open order at a time.",
                observed={"max_orders_per_day": max_orders_per_day, "open_orders": open_orders},
            ),
            CanaryCheck(
                name="halt_controls_enabled",
                passed=cls.bool_value(config, "global_kill_switch_available") and cls.bool_value(config, "auto_halt_after_any_anomaly"),
                severity="critical",
                detail="Canary requires global kill switch availability and automatic halt after any anomaly.",
                observed={
                    "global_kill_switch_available": config.get("global_kill_switch_available"),
                    "auto_halt_after_any_anomaly": config.get("auto_halt_after_any_anomaly"),
                },
            ),
            CanaryCheck(
                name="post_order_controls_required",
                passed=(
                    cls.bool_value(config, "post_order_reconciliation_required")
                    and cls.bool_value(config, "operator_alert_required")
                    and cls.bool_value(config, "canary_report_required")
                    and cls.bool_value(config, "canary_signoff_required")
                ),
                severity="critical",
                detail="Canary requires reconciliation, alerting, report, and signoff after any attempt.",
                observed={
                    "post_order_reconciliation_required": config.get("post_order_reconciliation_required"),
                    "operator_alert_required": config.get("operator_alert_required"),
                    "canary_report_required": config.get("canary_report_required"),
                    "canary_signoff_required": config.get("canary_signoff_required"),
                },
            ),
            CanaryCheck(
                name="scale_up_blocked_until_review",
                passed=cls.bool_value(config, "scale_up_blocked_until_review"),
                severity="critical",
                detail="Any scaling beyond one tiny canary must remain blocked until post-canary review.",
                observed={"scale_up_blocked_until_review": config.get("scale_up_blocked_until_review")},
            ),
            CanaryCheck(
                name="no_unresolved_state",
                passed=pending_reconciliation_count == 0 and unresolved_canary_report_count == 0 and active_halt_count == 0,
                severity="critical",
                detail="No pending reconciliation, unresolved canary report, or active halt may exist before canary consideration.",
                observed={
                    "pending_reconciliation_count": pending_reconciliation_count,
                    "unresolved_canary_report_count": unresolved_canary_report_count,
                    "active_halt_count": active_halt_count,
                },
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "canary_candidate_ready" if not blockers else "blocked",
            "ready_for_single_tiny_autonomous_canary_review": not blockers,
            "live_order_submission_by_this_service": False,
            "blockers": blockers,
            "checks": serialized,
            "policy": cls.policy(),
            "evaluated_at": cls.utc_now(),
        }

    @classmethod
    def evaluate_post_canary_review(cls, summary: Mapping[str, Any]) -> dict[str, Any]:
        def as_int(name: str) -> int:
            try:
                return int(summary.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0

        def as_float(name: str) -> float:
            try:
                return float(summary.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        attempted_orders = as_int("attempted_orders")
        filled_orders = as_int("filled_orders")
        anomaly_count = as_int("anomaly_count")
        reconciliation_issue_count = as_int("reconciliation_issue_count")
        alert_count = as_int("operator_alert_count")
        signed_off = cls.bool_value(summary, "operator_signoff_recorded")
        realized_loss_usd = as_float("realized_loss_usd")

        checks = [
            CanaryCheck(
                name="single_canary_attempt_only",
                passed=attempted_orders <= 1 and filled_orders <= 1,
                severity="critical",
                detail="Post-canary review must confirm no more than one attempt and fill occurred.",
                observed={"attempted_orders": attempted_orders, "filled_orders": filled_orders},
            ),
            CanaryCheck(
                name="no_anomalies_or_reconciliation_issues",
                passed=anomaly_count == 0 and reconciliation_issue_count == 0,
                severity="critical",
                detail="Canary cannot be approved if anomaly or reconciliation issues occurred.",
                observed={"anomaly_count": anomaly_count, "reconciliation_issue_count": reconciliation_issue_count},
            ),
            CanaryCheck(
                name="operator_alert_and_signoff_recorded",
                passed=alert_count >= attempted_orders and signed_off,
                severity="critical",
                detail="Operator alert and signoff must be recorded for the canary.",
                observed={"operator_alert_count": alert_count, "operator_signoff_recorded": signed_off},
            ),
            CanaryCheck(
                name="loss_within_tiny_limit",
                passed=realized_loss_usd <= cls.DEFAULT_MAX_DAILY_LOSS_USD,
                severity="critical",
                detail="Canary realized loss must remain within the tiny limit.",
                observed={"realized_loss_usd": realized_loss_usd, "limit": cls.DEFAULT_MAX_DAILY_LOSS_USD},
            ),
            CanaryCheck(
                name="scale_up_still_blocked",
                passed=cls.bool_value(summary, "scale_up_still_blocked"),
                severity="critical",
                detail="Scale-up must remain blocked after canary until a separate production release gate approves it.",
                observed={"scale_up_still_blocked": summary.get("scale_up_still_blocked")},
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "canary_review_passed" if not blockers else "blocked",
            "ready_for_production_release_review": not blockers,
            "blockers": blockers,
            "checks": serialized,
            "policy": cls.policy(),
            "evaluated_at": cls.utc_now(),
        }

    async def record_candidate_review(self, user_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        result = self.evaluate_candidate(config)
        await self.db.autonomous_canary_candidate_reviews.insert_one({
            "user_id": user_id,
            "config": dict(config),
            "result": result,
            "created_at": self.utc_now(),
        })
        return result

    async def record_post_canary_review(self, user_id: str, summary: Mapping[str, Any]) -> dict[str, Any]:
        result = self.evaluate_post_canary_review(summary)
        await self.db.autonomous_canary_post_reviews.insert_one({
            "user_id": user_id,
            "summary": dict(summary),
            "result": result,
            "created_at": self.utc_now(),
        })
        return result
