from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ReleaseCheck:
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


class Phase13PilotReleaseServiceV2:
    """Evaluates pilot evidence before limited repeated manual live trading.

    This service does not submit live orders. It converts pilot reports, signoffs,
    reconciliation state, and operator review decisions into a deterministic
    release decision for limited manual live trading.
    """

    APPROVING_DECISIONS = {"approved_for_next_tiny_pilot", "approved_for_limited_manual_live"}
    BLOCKING_DECISIONS = {"hold", "reject", "manual_investigation_required"}
    DEFAULT_MIN_REVIEWED_PILOTS = 1
    DEFAULT_MAX_RELEASE_NOTIONAL_USD = 10.0
    DEFAULT_ALLOWED_SYMBOLS = {"BTC-USD", "ETH-USD"}

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @classmethod
    def policy(cls) -> dict[str, Any]:
        return {
            "phase": "phase_13_pilot_review_and_limited_manual_release",
            "release_scope": "limited_repeated_manual_live_only",
            "autonomous_live_trading": "not_allowed",
            "minimum_reviewed_pilots": cls.DEFAULT_MIN_REVIEWED_PILOTS,
            "max_release_notional_usd": cls.DEFAULT_MAX_RELEASE_NOTIONAL_USD,
            "allowed_symbols": sorted(cls.DEFAULT_ALLOWED_SYMBOLS),
            "required_evidence": [
                "resolved_post_submit_reconciliation",
                "complete_pilot_report_with_hash",
                "valid_operator_signoff",
                "clear_expansion_status",
                "no_pending_reconciliation",
                "no_unsigned_completed_report",
                "no_blocking_operator_decision",
            ],
        }

    async def fetch_evidence(self, user_id: str, limit: int = 100) -> dict[str, Any]:
        reports = await self.db.manual_live_pilot_reports.find({"user_id": user_id}, {"_id": 0}).sort("generated_at", -1).limit(limit).to_list(limit)
        signoffs = await self.db.manual_live_pilot_signoffs.find({"user_id": user_id}, {"_id": 0}).sort("signed_at", -1).limit(limit).to_list(limit)
        requirements = await self.db.live_post_submit_reconciliation_requirements.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
        release_decisions = await self.db.manual_live_release_decisions.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
        return {
            "user_id": user_id,
            "reports": reports,
            "signoffs": signoffs,
            "requirements": requirements,
            "release_decisions": release_decisions,
            "fetched_at": self.utc_now(),
        }

    @classmethod
    def evaluate_evidence(
        cls,
        *,
        evidence: dict[str, Any],
        requested_max_notional_usd: float = DEFAULT_MAX_RELEASE_NOTIONAL_USD,
        requested_symbols: list[str] | None = None,
        minimum_reviewed_pilots: int = DEFAULT_MIN_REVIEWED_PILOTS,
    ) -> dict[str, Any]:
        reports = evidence.get("reports", []) or []
        signoffs = evidence.get("signoffs", []) or []
        requirements = evidence.get("requirements", []) or []
        requested_symbols = requested_symbols or sorted(cls.DEFAULT_ALLOWED_SYMBOLS)
        report_by_order = {report.get("live_order_id"): report for report in reports if report.get("live_order_id")}
        signoff_by_order = {signoff.get("live_order_id"): signoff for signoff in signoffs if signoff.get("live_order_id")}
        requirement_by_order = {item.get("live_order_id"): item for item in requirements if item.get("live_order_id")}

        complete_reports = [report for report in reports if report.get("status") == "complete" and report.get("report_hash")]
        reviewed_orders = []
        blocking_signoffs = []
        for order_id, report in report_by_order.items():
            signoff = signoff_by_order.get(order_id)
            requirement = requirement_by_order.get(order_id)
            if signoff and signoff.get("decision") in cls.BLOCKING_DECISIONS:
                blocking_signoffs.append(signoff)
            if (
                report.get("status") == "complete"
                and report.get("report_hash")
                and signoff
                and signoff.get("signoff_hash")
                and signoff.get("decision") in cls.APPROVING_DECISIONS
                and (not requirement or requirement.get("status") == "resolved")
            ):
                reviewed_orders.append(order_id)

        pending_requirements = [item for item in requirements if item.get("status") == "pending"]
        unsigned_complete_reports = [report for report in complete_reports if report.get("live_order_id") not in signoff_by_order]
        unknown_symbols = sorted(set(str(symbol).upper() for symbol in requested_symbols) - cls.DEFAULT_ALLOWED_SYMBOLS)
        try:
            requested_notional = float(requested_max_notional_usd)
        except (TypeError, ValueError):
            requested_notional = 0.0

        checks = [
            ReleaseCheck(
                name="minimum_reviewed_pilots_met",
                passed=len(reviewed_orders) >= int(minimum_reviewed_pilots),
                severity="critical",
                detail="At least the required number of complete, reconciled, signed-off pilot reports must exist.",
                observed={"reviewed_count": len(reviewed_orders), "required": minimum_reviewed_pilots, "reviewed_orders": reviewed_orders},
            ),
            ReleaseCheck(
                name="no_pending_reconciliation",
                passed=len(pending_requirements) == 0,
                severity="critical",
                detail="No post-submit reconciliation requirement may remain pending before limited manual release.",
                observed={"pending_count": len(pending_requirements)},
            ),
            ReleaseCheck(
                name="no_unsigned_complete_reports",
                passed=len(unsigned_complete_reports) == 0,
                severity="critical",
                detail="Every complete pilot report must have an operator signoff.",
                observed={"unsigned_count": len(unsigned_complete_reports)},
            ),
            ReleaseCheck(
                name="no_blocking_operator_signoff",
                passed=len(blocking_signoffs) == 0,
                severity="critical",
                detail="No pilot signoff may be hold, reject, or manual investigation required.",
                observed={"blocking_count": len(blocking_signoffs)},
            ),
            ReleaseCheck(
                name="requested_release_notional_remains_limited",
                passed=0.0 < requested_notional <= cls.DEFAULT_MAX_RELEASE_NOTIONAL_USD,
                severity="critical",
                detail="Limited manual release max notional must remain small.",
                observed={"requested_max_notional_usd": requested_notional, "cap": cls.DEFAULT_MAX_RELEASE_NOTIONAL_USD},
            ),
            ReleaseCheck(
                name="requested_symbols_are_allowlisted",
                passed=len(unknown_symbols) == 0,
                severity="critical",
                detail="Limited manual release symbols must remain restricted to the approved spot allowlist.",
                observed={"requested_symbols": requested_symbols, "unknown_symbols": unknown_symbols, "allowed_symbols": sorted(cls.DEFAULT_ALLOWED_SYMBOLS)},
            ),
        ]
        serialized = [check.to_dict() for check in checks]
        blockers = [check for check in serialized if check["severity"] == "critical" and not check["passed"]]
        return {
            "status": "approved_for_limited_manual_live" if not blockers else "blocked",
            "approved_for_limited_manual_live": not blockers,
            "blockers": blockers,
            "checks": serialized,
            "reviewed_orders": reviewed_orders,
            "policy": cls.policy(),
        }

    async def evaluate_release(
        self,
        *,
        user_id: str,
        requested_max_notional_usd: float = DEFAULT_MAX_RELEASE_NOTIONAL_USD,
        requested_symbols: list[str] | None = None,
        minimum_reviewed_pilots: int = DEFAULT_MIN_REVIEWED_PILOTS,
    ) -> dict[str, Any]:
        evidence = await self.fetch_evidence(user_id)
        result = self.evaluate_evidence(
            evidence=evidence,
            requested_max_notional_usd=requested_max_notional_usd,
            requested_symbols=requested_symbols,
            minimum_reviewed_pilots=minimum_reviewed_pilots,
        )
        record = {
            "user_id": user_id,
            "requested_max_notional_usd": requested_max_notional_usd,
            "requested_symbols": requested_symbols or sorted(self.DEFAULT_ALLOWED_SYMBOLS),
            "minimum_reviewed_pilots": minimum_reviewed_pilots,
            "result": result,
            "created_at": self.utc_now(),
        }
        await self.db.manual_live_release_decisions.insert_one(record)
        return record
