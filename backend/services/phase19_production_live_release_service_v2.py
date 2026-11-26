from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ProductionReleaseCheck:
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


class Phase19ProductionLiveReleaseServiceV2:
    """Final production live release gate.

    This service does not submit orders and does not enable execution. It
    evaluates whether the product, operations, evidence, and risk controls are
    complete enough for a controlled production-live release decision.
    """

    REQUIRED_RELEASE_MODES = {
        "paper",
        "live_readonly",
        "controlled_manual_live",
        "autonomous_shadow",
        "autonomous_canary",
        "production_live",
    }
    REQUIRED_RUNBOOKS = {
        "deployment_runbook",
        "rollback_runbook",
        "backup_restore_runbook",
        "incident_response_runbook",
        "secret_rotation_runbook",
        "manual_live_reconciliation_runbook",
        "autonomous_shadow_review_runbook",
        "autonomous_canary_review_runbook",
        "production_live_release_runbook",
    }
    REQUIRED_MONITORS = {
        "backend_health",
        "frontend_health",
        "worker_heartbeat",
        "database_connectivity",
        "market_data_freshness",
        "exchange_connectivity",
        "unresolved_reconciliation",
        "unsigned_report",
        "active_halt",
        "daily_loss",
        "drawdown",
        "open_orders",
        "order_rejection_rate",
    }
    REQUIRED_ALERTS = {
        "critical_ops",
        "reconciliation_blocker",
        "market_data_stale",
        "exchange_connectivity_degraded",
        "risk_limit_breach",
        "live_halt_triggered",
    }
    DEFAULT_MAX_ORDER_NOTIONAL_USD = 10.0
    DEFAULT_MAX_DAILY_NOTIONAL_USD = 50.0
    DEFAULT_MAX_DAILY_LOSS_USD = 10.0
    DEFAULT_MAX_DRAWDOWN_PCT = 2.0
    DEFAULT_MAX_OPEN_ORDERS = 1

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_19_production_live_trading_release_gate",
            "release_scope": "controlled_production_live_release_decision",
            "live_order_submission_by_this_service": "not_allowed",
            "required_release_modes": sorted(cls.REQUIRED_RELEASE_MODES),
            "required_runbooks": sorted(cls.REQUIRED_RUNBOOKS),
            "required_monitors": sorted(cls.REQUIRED_MONITORS),
            "required_alerts": sorted(cls.REQUIRED_ALERTS),
            "default_max_order_notional_usd": cls.DEFAULT_MAX_ORDER_NOTIONAL_USD,
            "default_max_daily_notional_usd": cls.DEFAULT_MAX_DAILY_NOTIONAL_USD,
            "default_max_daily_loss_usd": cls.DEFAULT_MAX_DAILY_LOSS_USD,
            "default_max_drawdown_pct": cls.DEFAULT_MAX_DRAWDOWN_PCT,
            "default_max_open_orders": cls.DEFAULT_MAX_OPEN_ORDERS,
            "required_evidence": [
                "phase15_controlled_manual_live_ready",
                "phase17_shadow_review_passed",
                "phase18_canary_review_passed",
                "phase14_operations_ready",
                "ci_green",
                "backup_restore_validated",
                "incident_response_ready",
                "separate_mode_gates_defined",
                "risk_limits_locked",
                "monitoring_and_alerting_ready",
                "no_unresolved_live_state",
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
    def evaluate_release(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        modes = {str(mode).strip() for mode in (config.get("release_modes") or [])}
        runbooks = {str(item).strip() for item in (config.get("runbooks") or [])}
        monitors = {str(item).strip() for item in (config.get("monitors") or [])}
        alerts = {str(item).strip() for item in (config.get("alerts") or [])}
        missing_modes = sorted(cls.REQUIRED_RELEASE_MODES - modes)
        missing_runbooks = sorted(cls.REQUIRED_RUNBOOKS - runbooks)
        missing_monitors = sorted(cls.REQUIRED_MONITORS - monitors)
        missing_alerts = sorted(cls.REQUIRED_ALERTS - alerts)

        def as_int(name: str) -> int:
            try:
                return int(config.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0

        def as_float(name: str) -> float:
            try:
                return float(config.get(name, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        max_order_notional = as_float("max_order_notional_usd")
        max_daily_notional = as_float("max_daily_notional_usd")
        max_daily_loss = as_float("max_daily_loss_usd")
        max_drawdown_pct = as_float("max_drawdown_pct")
        max_open_orders = as_int("max_open_orders")
        pending_reconciliation_count = as_int("pending_reconciliation_count")
        unsigned_report_count = as_int("unsigned_report_count")
        active_halt_count = as_int("active_halt_count")
        stale_worker_count = as_int("stale_worker_count")
        open_order_count = as_int("open_order_count")

        checks: list[ProductionReleaseCheck] = [
            ProductionReleaseCheck(
                name="phase_prerequisites_complete",
                passed=(
                    cls.bool_value(config, "phase15_controlled_manual_live_ready")
                    and cls.bool_value(config, "phase17_shadow_review_passed")
                    and cls.bool_value(config, "phase18_canary_review_passed")
                    and cls.bool_value(config, "phase14_operations_ready")
                ),
                severity="critical",
                detail="Manual-live, shadow, canary, and operations readiness must all be approved before production release.",
                observed={
                    "phase15_controlled_manual_live_ready": config.get("phase15_controlled_manual_live_ready"),
                    "phase17_shadow_review_passed": config.get("phase17_shadow_review_passed"),
                    "phase18_canary_review_passed": config.get("phase18_canary_review_passed"),
                    "phase14_operations_ready": config.get("phase14_operations_ready"),
                },
            ),
            ProductionReleaseCheck(
                name="release_modes_are_separately_gated",
                passed=not missing_modes,
                severity="critical",
                detail="Every live mode must remain separately gated and explicitly represented in release policy.",
                observed={"missing_modes": missing_modes, "configured_modes": sorted(modes)},
            ),
            ProductionReleaseCheck(
                name="runbooks_complete",
                passed=not missing_runbooks,
                severity="critical",
                detail="Production release requires complete deployment, rollback, backup, incident, secret, reconciliation, shadow, canary, and release runbooks.",
                observed={"missing_runbooks": missing_runbooks, "configured_runbooks": sorted(runbooks)},
            ),
            ProductionReleaseCheck(
                name="monitoring_complete",
                passed=not missing_monitors,
                severity="critical",
                detail="Production release requires complete monitoring across health, workers, database, market data, exchange, reconciliation, reports, halts, and risk.",
                observed={"missing_monitors": missing_monitors, "configured_monitors": sorted(monitors)},
            ),
            ProductionReleaseCheck(
                name="alerting_complete",
                passed=not missing_alerts,
                severity="critical",
                detail="Production release requires critical alert coverage for operations, reconciliation, data, exchange, risk, and halts.",
                observed={"missing_alerts": missing_alerts, "configured_alerts": sorted(alerts)},
            ),
            ProductionReleaseCheck(
                name="ci_and_supply_chain_checks_green",
                passed=cls.bool_value(config, "ci_green") and cls.bool_value(config, "dependency_audit_green") and cls.bool_value(config, "security_scan_green"),
                severity="critical",
                detail="CI, dependency audit, and security scan must be green for production release.",
                observed={
                    "ci_green": config.get("ci_green"),
                    "dependency_audit_green": config.get("dependency_audit_green"),
                    "security_scan_green": config.get("security_scan_green"),
                },
            ),
            ProductionReleaseCheck(
                name="backup_restore_and_rollback_validated",
                passed=cls.bool_value(config, "backup_restore_validated") and cls.bool_value(config, "rollback_validated"),
                severity="critical",
                detail="Backup/restore and rollback must be validated before production release.",
                observed={
                    "backup_restore_validated": config.get("backup_restore_validated"),
                    "rollback_validated": config.get("rollback_validated"),
                },
            ),
            ProductionReleaseCheck(
                name="incident_response_ready",
                passed=cls.bool_value(config, "incident_response_ready") and bool(config.get("incident_commander")),
                severity="critical",
                detail="Incident response must be ready and an incident owner must be assigned.",
                observed={
                    "incident_response_ready": config.get("incident_response_ready"),
                    "incident_commander_configured": bool(config.get("incident_commander")),
                },
            ),
            ProductionReleaseCheck(
                name="risk_limits_locked",
                passed=(
                    0.0 < max_order_notional <= cls.DEFAULT_MAX_ORDER_NOTIONAL_USD
                    and 0.0 < max_daily_notional <= cls.DEFAULT_MAX_DAILY_NOTIONAL_USD
                    and 0.0 < max_daily_loss <= cls.DEFAULT_MAX_DAILY_LOSS_USD
                    and 0.0 < max_drawdown_pct <= cls.DEFAULT_MAX_DRAWDOWN_PCT
                    and 0 < max_open_orders <= cls.DEFAULT_MAX_OPEN_ORDERS
                ),
                severity="critical",
                detail="Production release risk limits must remain conservative and locked.",
                observed={
                    "max_order_notional_usd": max_order_notional,
                    "max_daily_notional_usd": max_daily_notional,
                    "max_daily_loss_usd": max_daily_loss,
                    "max_drawdown_pct": max_drawdown_pct,
                    "max_open_orders": max_open_orders,
                },
            ),
            ProductionReleaseCheck(
                name="kill_switch_and_halt_controls_ready",
                passed=cls.bool_value(config, "global_kill_switch_ready") and cls.bool_value(config, "automatic_halt_ready") and cls.bool_value(config, "manual_override_ready"),
                severity="critical",
                detail="Global kill switch, automatic halt, and manual override must all be ready.",
                observed={
                    "global_kill_switch_ready": config.get("global_kill_switch_ready"),
                    "automatic_halt_ready": config.get("automatic_halt_ready"),
                    "manual_override_ready": config.get("manual_override_ready"),
                },
            ),
            ProductionReleaseCheck(
                name="post_order_controls_locked",
                passed=cls.bool_value(config, "post_order_reconciliation_required") and cls.bool_value(config, "post_order_report_required") and cls.bool_value(config, "post_order_signoff_required"),
                severity="critical",
                detail="Every production-live order must require reconciliation, report, and signoff.",
                observed={
                    "post_order_reconciliation_required": config.get("post_order_reconciliation_required"),
                    "post_order_report_required": config.get("post_order_report_required"),
                    "post_order_signoff_required": config.get("post_order_signoff_required"),
                },
            ),
            ProductionReleaseCheck(
                name="no_unresolved_live_state",
                passed=(
                    pending_reconciliation_count == 0
                    and unsigned_report_count == 0
                    and active_halt_count == 0
                    and stale_worker_count == 0
                    and open_order_count <= max_open_orders
                ),
                severity="critical",
                detail="No unresolved live state, active halt, stale worker, or excess open order may exist at release.",
                observed={
                    "pending_reconciliation_count": pending_reconciliation_count,
                    "unsigned_report_count": unsigned_report_count,
                    "active_halt_count": active_halt_count,
                    "stale_worker_count": stale_worker_count,
                    "open_order_count": open_order_count,
                    "max_open_orders": max_open_orders,
                },
            ),
            ProductionReleaseCheck(
                name="production_release_approval_recorded",
                passed=cls.bool_value(config, "production_release_approval_recorded") and bool(config.get("release_approver")),
                severity="critical",
                detail="A production release approval and approver identity must be recorded.",
                observed={
                    "production_release_approval_recorded": config.get("production_release_approval_recorded"),
                    "release_approver_configured": bool(config.get("release_approver")),
                },
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "production_release_ready" if not blockers else "blocked",
            "ready_for_production_live_release": not blockers,
            "live_order_submission_by_this_service": False,
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
        stale_workers = await self.db.worker_heartbeats.find({"status": "stale"}, {"_id": 0}).limit(100).to_list(100)
        return {
            "pending_reconciliation_count": len(pending),
            "unsigned_report_count": len(unsigned_reports),
            "active_halt_count": len(active_halts),
            "stale_worker_count": len(stale_workers),
            "collected_at": self.utc_now(),
        }

    async def evaluate(self, user_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        live_state = await self.collect_live_state(user_id)
        merged = {**dict(config), **live_state}
        result = self.evaluate_release(merged)
        await self.db.production_live_release_reviews.insert_one({
            "user_id": user_id,
            "config": dict(config),
            "live_state": live_state,
            "result": result,
            "created_at": self.utc_now(),
        })
        return result
