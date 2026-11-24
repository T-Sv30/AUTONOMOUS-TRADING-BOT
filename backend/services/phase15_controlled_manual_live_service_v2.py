from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ControlledManualCheck:
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


class Phase15ControlledManualLiveServiceV2:
    """Release gate for controlled repeated manual live trading.

    This service does not submit live orders and does not enable autonomous live
    trading. It determines whether the system may enter a limited manual-live
    operating window after pilot evidence and operations readiness are approved.
    """

    DEFAULT_ALLOWED_SYMBOLS = {"BTC-USD", "ETH-USD"}
    DEFAULT_MAX_ORDER_NOTIONAL_USD = 10.0
    DEFAULT_MAX_DAILY_NOTIONAL_USD = 50.0
    DEFAULT_MAX_ORDERS_PER_DAY = 5
    DEFAULT_MAX_OPEN_LIVE_ORDERS = 1
    REQUIRED_MODE = "controlled_manual_live"

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_15_controlled_manual_live_trading",
            "release_scope": "limited_repeated_manual_live_only",
            "autonomous_live_trading": "not_allowed",
            "required_mode": cls.REQUIRED_MODE,
            "allowed_symbols": sorted(cls.DEFAULT_ALLOWED_SYMBOLS),
            "max_order_notional_usd": cls.DEFAULT_MAX_ORDER_NOTIONAL_USD,
            "max_daily_notional_usd": cls.DEFAULT_MAX_DAILY_NOTIONAL_USD,
            "max_orders_per_day": cls.DEFAULT_MAX_ORDERS_PER_DAY,
            "max_open_live_orders": cls.DEFAULT_MAX_OPEN_LIVE_ORDERS,
            "required_controls": [
                "phase13_limited_manual_release_approved",
                "phase14_operations_ready",
                "manual_approval_required_for_every_order",
                "signed_approval_required_for_every_order",
                "dry_run_required_before_every_order",
                "post_order_reconciliation_required",
                "pilot_report_required_after_every_order",
                "operator_signoff_required_after_every_order",
                "kill_switch_closed_when_idle",
                "symbol_allowlist_enforced",
                "order_and_daily_notional_caps_enforced",
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
    def evaluate_config(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        allowed_symbols = {str(symbol).upper() for symbol in (config.get("allowed_symbols") or [])}
        unknown_symbols = sorted(allowed_symbols - cls.DEFAULT_ALLOWED_SYMBOLS)
        try:
            max_order_notional = float(config.get("max_order_notional_usd", 0) or 0)
        except (TypeError, ValueError):
            max_order_notional = 0.0
        try:
            max_daily_notional = float(config.get("max_daily_notional_usd", 0) or 0)
        except (TypeError, ValueError):
            max_daily_notional = 0.0
        try:
            max_orders_per_day = int(config.get("max_orders_per_day", 0) or 0)
        except (TypeError, ValueError):
            max_orders_per_day = 0
        try:
            open_live_orders = int(config.get("open_live_orders", 0) or 0)
        except (TypeError, ValueError):
            open_live_orders = 0
        try:
            pending_reconciliation = int(config.get("pending_reconciliation_count", 0) or 0)
        except (TypeError, ValueError):
            pending_reconciliation = 0
        try:
            unsigned_reports = int(config.get("unsigned_completed_report_count", 0) or 0)
        except (TypeError, ValueError):
            unsigned_reports = 0
        try:
            active_halts = int(config.get("active_halt_count", 0) or 0)
        except (TypeError, ValueError):
            active_halts = 0

        checks: list[ControlledManualCheck] = [
            ControlledManualCheck(
                name="phase13_release_approved",
                passed=cls.bool_value(config, "phase13_release_approved"),
                severity="critical",
                detail="Phase 13 must approve limited manual live release from reviewed pilot evidence.",
                observed={"phase13_release_approved": config.get("phase13_release_approved")},
            ),
            ControlledManualCheck(
                name="phase14_operations_ready",
                passed=cls.bool_value(config, "phase14_operations_ready"),
                severity="critical",
                detail="Phase 14 operations readiness must be approved before controlled manual live trading.",
                observed={"phase14_operations_ready": config.get("phase14_operations_ready")},
            ),
            ControlledManualCheck(
                name="controlled_manual_mode_explicit",
                passed=config.get("mode") == cls.REQUIRED_MODE,
                severity="critical",
                detail="Controlled manual live trading must use an explicit dedicated mode.",
                observed={"mode": config.get("mode"), "required_mode": cls.REQUIRED_MODE},
            ),
            ControlledManualCheck(
                name="autonomous_live_disabled",
                passed=not cls.bool_value(config, "autonomous_live_enabled"),
                severity="critical",
                detail="Autonomous live trading remains disallowed in Phase 15.",
                observed={"autonomous_live_enabled": config.get("autonomous_live_enabled")},
            ),
            ControlledManualCheck(
                name="manual_approval_required",
                passed=cls.bool_value(config, "manual_approval_required"),
                severity="critical",
                detail="Every controlled manual live order must require explicit human approval.",
                observed={"manual_approval_required": config.get("manual_approval_required")},
            ),
            ControlledManualCheck(
                name="signed_approval_required",
                passed=cls.bool_value(config, "signed_approval_required"),
                severity="critical",
                detail="Every controlled manual live order must require signed approval.",
                observed={"signed_approval_required": config.get("signed_approval_required")},
            ),
            ControlledManualCheck(
                name="dry_run_required_before_each_order",
                passed=cls.bool_value(config, "dry_run_required_before_each_order"),
                severity="critical",
                detail="Every intended order must be rehearsed with an exact dry run first.",
                observed={"dry_run_required_before_each_order": config.get("dry_run_required_before_each_order")},
            ),
            ControlledManualCheck(
                name="post_order_reconciliation_required",
                passed=cls.bool_value(config, "post_order_reconciliation_required"),
                severity="critical",
                detail="Every live order must be followed by live-readonly verification and reconciliation.",
                observed={"post_order_reconciliation_required": config.get("post_order_reconciliation_required")},
            ),
            ControlledManualCheck(
                name="post_order_report_and_signoff_required",
                passed=cls.bool_value(config, "pilot_report_required_after_each_order") and cls.bool_value(config, "operator_signoff_required_after_each_order"),
                severity="critical",
                detail="Every controlled manual live order must produce a report and operator signoff.",
                observed={
                    "pilot_report_required_after_each_order": config.get("pilot_report_required_after_each_order"),
                    "operator_signoff_required_after_each_order": config.get("operator_signoff_required_after_each_order"),
                },
            ),
            ControlledManualCheck(
                name="kill_switch_closed_when_idle",
                passed=cls.bool_value(config, "kill_switch_closed_when_idle"),
                severity="critical",
                detail="The live execution kill switch must be closed when not inside an approved submit window.",
                observed={"kill_switch_closed_when_idle": config.get("kill_switch_closed_when_idle")},
            ),
            ControlledManualCheck(
                name="symbols_are_allowlisted",
                passed=bool(allowed_symbols) and not unknown_symbols,
                severity="critical",
                detail="Controlled manual live symbols must remain inside the approved allowlist.",
                observed={"allowed_symbols": sorted(allowed_symbols), "unknown_symbols": unknown_symbols},
            ),
            ControlledManualCheck(
                name="max_order_notional_limited",
                passed=0.0 < max_order_notional <= cls.DEFAULT_MAX_ORDER_NOTIONAL_USD,
                severity="critical",
                detail="Per-order notional must remain within the controlled manual live cap.",
                observed={"max_order_notional_usd": max_order_notional, "cap": cls.DEFAULT_MAX_ORDER_NOTIONAL_USD},
            ),
            ControlledManualCheck(
                name="max_daily_notional_limited",
                passed=0.0 < max_daily_notional <= cls.DEFAULT_MAX_DAILY_NOTIONAL_USD,
                severity="critical",
                detail="Daily notional must remain within the controlled manual live cap.",
                observed={"max_daily_notional_usd": max_daily_notional, "cap": cls.DEFAULT_MAX_DAILY_NOTIONAL_USD},
            ),
            ControlledManualCheck(
                name="order_frequency_limited",
                passed=0 < max_orders_per_day <= cls.DEFAULT_MAX_ORDERS_PER_DAY,
                severity="critical",
                detail="Controlled manual live trading must limit daily order count.",
                observed={"max_orders_per_day": max_orders_per_day, "cap": cls.DEFAULT_MAX_ORDERS_PER_DAY},
            ),
            ControlledManualCheck(
                name="open_live_orders_limited",
                passed=open_live_orders <= cls.DEFAULT_MAX_OPEN_LIVE_ORDERS,
                severity="critical",
                detail="Only one live order may be open at a time during controlled manual live trading.",
                observed={"open_live_orders": open_live_orders, "cap": cls.DEFAULT_MAX_OPEN_LIVE_ORDERS},
            ),
            ControlledManualCheck(
                name="no_unresolved_live_state",
                passed=pending_reconciliation == 0 and unsigned_reports == 0 and active_halts == 0,
                severity="critical",
                detail="No unresolved reconciliation, unsigned report, or active halt may exist before controlled manual live operation.",
                observed={
                    "pending_reconciliation_count": pending_reconciliation,
                    "unsigned_completed_report_count": unsigned_reports,
                    "active_halt_count": active_halts,
                },
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "controlled_manual_live_ready" if not blockers else "blocked",
            "ready_for_controlled_manual_live": not blockers,
            "blockers": blockers,
            "checks": serialized,
            "policy": cls.policy(),
            "evaluated_at": cls.utc_now(),
        }

    async def collect_live_state(self, user_id: str) -> dict[str, Any]:
        pending = await self.db.live_post_submit_reconciliation_requirements.find({"user_id": user_id, "status": "pending"}, {"_id": 0}).limit(100).to_list(100)
        complete_reports = await self.db.manual_live_pilot_reports.find({"user_id": user_id, "status": "complete"}, {"_id": 0}).limit(100).to_list(100)
        signoffs = await self.db.manual_live_pilot_signoffs.find({"user_id": user_id}, {"_id": 0}).limit(100).to_list(100)
        signed_order_ids = {item.get("live_order_id") for item in signoffs}
        unsigned_reports = [report for report in complete_reports if report.get("live_order_id") not in signed_order_ids]
        active_halts = await self.db.live_halts.find({"active": True}, {"_id": 0}).limit(100).to_list(100)
        return {
            "pending_reconciliation_count": len(pending),
            "unsigned_completed_report_count": len(unsigned_reports),
            "active_halt_count": len(active_halts),
            "collected_at": self.utc_now(),
        }

    async def evaluate(self, user_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        live_state = await self.collect_live_state(user_id)
        merged = {**dict(config), **live_state}
        result = self.evaluate_config(merged)
        await self.db.controlled_manual_live_readiness_reports.insert_one({
            "user_id": user_id,
            "config": dict(config),
            "live_state": live_state,
            "result": result,
            "created_at": self.utc_now(),
        })
        return result
