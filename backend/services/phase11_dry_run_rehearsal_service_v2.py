from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RehearsalCheck:
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


class Phase11DryRunRehearsalServiceV2:
    """Validates the full manual-live dry-run rehearsal artifact set.

    This service does not place orders. It validates the output produced by the
    existing Phase 5 through Phase 8 workflow:

    - pilot readiness;
    - exact manual order dry-run;
    - post-submit/pilot report state;
    - operator signoff/expansion state.
    """

    REQUIRED_DRY_RUN_FIELDS = {
        "live_order_id",
        "gate",
        "risk_decision",
        "audit",
        "reconciliation_requirement",
    }

    @staticmethod
    def is_dict(value: Any) -> bool:
        return isinstance(value, dict)

    @classmethod
    def plan(cls) -> dict[str, Any]:
        return {
            "phase": "phase_11_full_dry_run_dress_rehearsal",
            "live_order_submission": "disabled_by_design",
            "steps": [
                {"order": 1, "name": "backend_startup", "required": True},
                {"order": 2, "name": "frontend_build", "required": True},
                {"order": 3, "name": "secret_hardening_check", "required": True},
                {"order": 4, "name": "live_readonly_snapshot", "required": True},
                {"order": 5, "name": "live_readonly_reconciliation", "required": True},
                {"order": 6, "name": "pilot_readiness", "required": True},
                {"order": 7, "name": "exact_manual_order_dry_run", "required": True},
                {"order": 8, "name": "pilot_report_generation", "required": True},
                {"order": 9, "name": "operator_signoff", "required": True},
                {"order": 10, "name": "expansion_status_clear", "required": True},
            ],
            "expected_dry_run_fields": sorted(cls.REQUIRED_DRY_RUN_FIELDS),
        }

    @classmethod
    def validate_readiness(cls, readiness: dict[str, Any]) -> list[RehearsalCheck]:
        return [
            RehearsalCheck(
                name="pilot_readiness_ready",
                passed=bool(readiness.get("ready")) and readiness.get("status") == "ready",
                severity="critical",
                detail="Pilot readiness must be ready before the exact manual live dry run.",
                observed={"ready": readiness.get("ready"), "status": readiness.get("status"), "blocker_count": len(readiness.get("blockers", []) or [])},
            ),
            RehearsalCheck(
                name="pilot_readiness_has_no_blockers",
                passed=len(readiness.get("blockers", []) or []) == 0,
                severity="critical",
                detail="Pilot readiness blockers must be empty.",
                observed={"blockers": readiness.get("blockers", [])},
            ),
        ]

    @classmethod
    def validate_dry_run_order(cls, dry_run_order: dict[str, Any]) -> list[RehearsalCheck]:
        order = dry_run_order.get("order") if isinstance(dry_run_order.get("order"), dict) else {}
        reconciliation = dry_run_order.get("reconciliation_requirement") if isinstance(dry_run_order.get("reconciliation_requirement"), dict) else {}
        missing_fields = sorted(field for field in cls.REQUIRED_DRY_RUN_FIELDS if field not in dry_run_order)
        gate = dry_run_order.get("gate") if isinstance(dry_run_order.get("gate"), dict) else {}
        risk_decision = dry_run_order.get("risk_decision") if isinstance(dry_run_order.get("risk_decision"), dict) else {}

        return [
            RehearsalCheck(
                name="dry_run_contains_required_metadata",
                passed=not missing_fields,
                severity="critical",
                detail="Dry-run manual order response must include lifecycle, gate, risk, audit, and reconciliation metadata.",
                observed={"missing_fields": missing_fields},
            ),
            RehearsalCheck(
                name="dry_run_does_not_report_live_execution",
                passed=order.get("live_execution") is not True,
                severity="critical",
                detail="The dress rehearsal order must not execute live.",
                observed={"live_execution": order.get("live_execution"), "status": dry_run_order.get("status") or order.get("status")},
            ),
            RehearsalCheck(
                name="dry_run_order_status_is_dry_run",
                passed=(dry_run_order.get("status") == "dry_run" or order.get("status") == "dry_run"),
                severity="critical",
                detail="The exact manual order rehearsal must use dry_run=true and return dry_run status.",
                observed={"response_status": dry_run_order.get("status"), "order_status": order.get("status")},
            ),
            RehearsalCheck(
                name="dry_run_gate_allowed",
                passed=bool(gate.get("allowed")) and gate.get("dry_run") is True,
                severity="critical",
                detail="The dry-run order must pass the real live gate while marked dry_run.",
                observed={"allowed": gate.get("allowed"), "dry_run": gate.get("dry_run"), "reason": gate.get("reason")},
            ),
            RehearsalCheck(
                name="dry_run_risk_allows_order",
                passed=risk_decision.get("decision") == "allow",
                severity="critical",
                detail="Risk decision must allow the exact dry-run order before a tiny live pilot is considered.",
                observed={"decision": risk_decision.get("decision"), "reason": risk_decision.get("reason")},
            ),
            RehearsalCheck(
                name="dry_run_reconciliation_not_required",
                passed=reconciliation.get("required") is False or reconciliation.get("status") in {"not_required", "skipped"},
                severity="critical",
                detail="Dry-run orders must not create pending post-submit reconciliation requirements.",
                observed=reconciliation,
            ),
        ]

    @classmethod
    def validate_report_and_signoff(cls, report: dict[str, Any], expansion_status: dict[str, Any]) -> list[RehearsalCheck]:
        return [
            RehearsalCheck(
                name="pilot_report_generated",
                passed=bool(report.get("report_hash")) and report.get("status") in {"complete", "incomplete"},
                severity="critical",
                detail="Dress rehearsal must produce a pilot report artifact with a report hash.",
                observed={"status": report.get("status"), "report_hash_present": bool(report.get("report_hash"))},
            ),
            RehearsalCheck(
                name="expansion_status_clear_after_signoff",
                passed=bool(expansion_status.get("allowed_to_repeat_pilot")) and not expansion_status.get("blockers"),
                severity="critical",
                detail="After rehearsal report/signoff, expansion status should be clear for the next reviewed tiny pilot.",
                observed={"allowed_to_repeat_pilot": expansion_status.get("allowed_to_repeat_pilot"), "blockers": expansion_status.get("blockers", [])},
            ),
        ]

    @classmethod
    def validate_artifacts(
        cls,
        *,
        readiness: dict[str, Any],
        dry_run_order: dict[str, Any],
        report: dict[str, Any],
        expansion_status: dict[str, Any],
    ) -> dict[str, Any]:
        checks = []
        checks.extend(cls.validate_readiness(readiness or {}))
        checks.extend(cls.validate_dry_run_order(dry_run_order or {}))
        checks.extend(cls.validate_report_and_signoff(report or {}, expansion_status or {}))
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "passed" if not blockers else "failed",
            "ready_for_tiny_manual_live_pilot": not blockers,
            "blockers": blockers,
            "checks": serialized,
            "plan": cls.plan(),
        }
