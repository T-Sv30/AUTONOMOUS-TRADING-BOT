from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class OperationsCheck:
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


class Phase14OperationsHardeningServiceV2:
    """Production-operations hardening gate for controlled manual live trading.

    This service does not submit orders. It evaluates whether the runtime and
    operator process have the minimum operational controls required before the
    project moves into limited repeated manual live trading.
    """

    REQUIRED_RUNBOOKS = {
        "deployment_runbook",
        "rollback_runbook",
        "backup_restore_runbook",
        "incident_response_runbook",
        "secret_rotation_runbook",
        "manual_live_reconciliation_runbook",
    }
    REQUIRED_MONITORS = {
        "backend_health",
        "frontend_health",
        "worker_heartbeat",
        "database_connectivity",
        "unresolved_reconciliation",
        "unsigned_pilot_report",
        "live_halt_active",
    }
    REQUIRED_ALERT_CHANNELS = {"critical_ops", "manual_live_reconciliation"}

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_14_production_operations_hardening",
            "release_scope": "operations_ready_for_controlled_manual_live",
            "autonomous_live_trading": "not_allowed",
            "required_runbooks": sorted(cls.REQUIRED_RUNBOOKS),
            "required_monitors": sorted(cls.REQUIRED_MONITORS),
            "required_alert_channels": sorted(cls.REQUIRED_ALERT_CHANNELS),
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
        runbooks = set(config.get("runbooks", []) or [])
        monitors = set(config.get("monitors", []) or [])
        alert_channels = set(config.get("alert_channels", []) or [])
        pending_reconciliation_count = int(config.get("pending_reconciliation_count", 0) or 0)
        unsigned_report_count = int(config.get("unsigned_completed_report_count", 0) or 0)
        active_halt_count = int(config.get("active_halt_count", 0) or 0)
        stale_worker_count = int(config.get("stale_worker_count", 0) or 0)

        missing_runbooks = sorted(cls.REQUIRED_RUNBOOKS - runbooks)
        missing_monitors = sorted(cls.REQUIRED_MONITORS - monitors)
        missing_alert_channels = sorted(cls.REQUIRED_ALERT_CHANNELS - alert_channels)

        checks: list[OperationsCheck] = [
            OperationsCheck(
                name="deployment_runbooks_complete",
                passed=not missing_runbooks,
                severity="critical",
                detail="Production deployment, rollback, backup/restore, incident response, secret rotation, and reconciliation runbooks must exist.",
                observed={"missing_runbooks": missing_runbooks, "configured_runbooks": sorted(runbooks)},
            ),
            OperationsCheck(
                name="monitoring_coverage_complete",
                passed=not missing_monitors,
                severity="critical",
                detail="Production monitoring must cover health, workers, database, reconciliation, unsigned reports, and live halts.",
                observed={"missing_monitors": missing_monitors, "configured_monitors": sorted(monitors)},
            ),
            OperationsCheck(
                name="critical_alert_channels_configured",
                passed=not missing_alert_channels,
                severity="critical",
                detail="Critical operational and reconciliation alert channels must be configured.",
                observed={"missing_alert_channels": missing_alert_channels, "configured_alert_channels": sorted(alert_channels)},
            ),
            OperationsCheck(
                name="database_backup_enabled",
                passed=cls.bool_value(config, "database_backup_enabled"),
                severity="critical",
                detail="Database backup must be enabled before controlled manual live trading.",
                observed={"database_backup_enabled": config.get("database_backup_enabled")},
            ),
            OperationsCheck(
                name="database_restore_drill_recorded",
                passed=cls.bool_value(config, "database_restore_drill_recorded"),
                severity="critical",
                detail="A restore drill must be recorded before production live operation.",
                observed={"database_restore_drill_recorded": config.get("database_restore_drill_recorded")},
            ),
            OperationsCheck(
                name="log_redaction_enabled",
                passed=cls.bool_value(config, "log_redaction_enabled"),
                severity="critical",
                detail="Logs must redact secrets and exchange credential material.",
                observed={"log_redaction_enabled": config.get("log_redaction_enabled")},
            ),
            OperationsCheck(
                name="error_tracking_configured",
                passed=cls.bool_value(config, "error_tracking_configured"),
                severity="warning",
                detail="Error tracking should be configured for production incident triage.",
                observed={"error_tracking_configured": config.get("error_tracking_configured")},
            ),
            OperationsCheck(
                name="rate_limiting_enabled",
                passed=cls.bool_value(config, "rate_limiting_enabled"),
                severity="critical",
                detail="API rate limiting must be enabled for production operation.",
                observed={"rate_limiting_enabled": config.get("rate_limiting_enabled")},
            ),
            OperationsCheck(
                name="production_cors_explicit",
                passed=cls.bool_value(config, "production_cors_explicit"),
                severity="critical",
                detail="Production CORS must be explicit and must not use wildcard origins.",
                observed={"production_cors_explicit": config.get("production_cors_explicit")},
            ),
            OperationsCheck(
                name="no_unresolved_live_state",
                passed=pending_reconciliation_count == 0 and unsigned_report_count == 0 and active_halt_count == 0,
                severity="critical",
                detail="No unresolved reconciliation, unsigned report, or active halt may exist before controlled manual live trading.",
                observed={
                    "pending_reconciliation_count": pending_reconciliation_count,
                    "unsigned_completed_report_count": unsigned_report_count,
                    "active_halt_count": active_halt_count,
                },
            ),
            OperationsCheck(
                name="workers_not_stale",
                passed=stale_worker_count == 0,
                severity="critical",
                detail="No required worker heartbeat may be stale before production operation.",
                observed={"stale_worker_count": stale_worker_count},
            ),
            OperationsCheck(
                name="incident_commander_assigned",
                passed=bool(config.get("incident_commander")),
                severity="warning",
                detail="An incident owner should be assigned for live-operation windows.",
                observed={"incident_commander_configured": bool(config.get("incident_commander"))},
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        warnings = [check for check in serialized if check["severity"] == "warning" and not check["passed"]]
        return {
            "status": "operations_ready" if not blockers else "blocked",
            "ready_for_controlled_manual_live": not blockers,
            "blockers": blockers,
            "warnings": warnings,
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
            "unsigned_completed_report_count": len(unsigned_reports),
            "active_halt_count": len(active_halts),
            "stale_worker_count": len(stale_workers),
            "collected_at": self.utc_now(),
        }

    async def evaluate(self, user_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        live_state = await self.collect_live_state(user_id)
        merged = {**dict(config), **live_state}
        result = self.evaluate_config(merged)
        await self.db.production_operations_readiness_reports.insert_one({
            "user_id": user_id,
            "config": dict(config),
            "live_state": live_state,
            "result": result,
            "created_at": self.utc_now(),
        })
        return result
