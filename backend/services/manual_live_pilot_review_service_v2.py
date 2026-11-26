import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from services.alert_service import AlertService


class ManualLivePilotReviewServiceV2:
    """Operator review and expansion-control layer for manual live pilots.

    This service does not submit orders. It controls what happens after a pilot:
    signoff, unresolved reconciliation alerts, and prevention of repeated pilots
    while prior reports remain unsigned or reconciliation remains pending.
    """

    VALID_DECISIONS = {"approved_for_next_tiny_pilot", "hold", "reject", "manual_investigation_required"}

    def __init__(self, db):
        self.db = db
        self.alerts = AlertService(db)

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def canonical_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

    async def latest_signoff(self, user_id: str, live_order_id: str) -> dict[str, Any] | None:
        return await self.db.manual_live_pilot_signoffs.find_one(
            {"user_id": user_id, "live_order_id": live_order_id},
            {"_id": 0},
            sort=[("signed_at", -1)],
        )

    async def unsigned_completed_reports(self, user_id: str, limit: int = 100) -> dict[str, Any]:
        reports = await self.db.manual_live_pilot_reports.find({"user_id": user_id, "status": "complete"}, {"_id": 0}).sort("generated_at", -1).limit(limit).to_list(limit)
        unsigned = []
        for report in reports:
            signoff = await self.latest_signoff(user_id, report.get("live_order_id"))
            if not signoff:
                unsigned.append(report)
        return {"user_id": user_id, "unsigned_count": len(unsigned), "unsigned_reports": unsigned, "checked_at": self.utc_now()}

    async def unresolved_reconciliation_requirements(self, user_id: str, limit: int = 100) -> dict[str, Any]:
        pending = await self.db.live_post_submit_reconciliation_requirements.find(
            {"user_id": user_id, "status": "pending"},
            {"_id": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)
        return {"user_id": user_id, "pending_count": len(pending), "pending": pending, "checked_at": self.utc_now()}

    async def expansion_status(self, user_id: str) -> dict[str, Any]:
        unresolved = await self.unresolved_reconciliation_requirements(user_id)
        unsigned = await self.unsigned_completed_reports(user_id)
        blockers = []
        if unresolved["pending_count"] > 0:
            blockers.append({"name": "pending_post_submit_reconciliation", "severity": "critical", "count": unresolved["pending_count"]})
        if unsigned["unsigned_count"] > 0:
            blockers.append({"name": "completed_pilot_report_without_signoff", "severity": "critical", "count": unsigned["unsigned_count"]})
        return {
            "user_id": user_id,
            "status": "blocked" if blockers else "clear_for_next_reviewed_tiny_pilot",
            "allowed_to_repeat_pilot": not blockers,
            "checked_at": self.utc_now(),
            "blockers": blockers,
            "unresolved_reconciliation": unresolved,
            "unsigned_completed_reports": unsigned,
        }

    async def signoff_report(self, *, user_id: str, live_order_id: str, operator_id: str, decision: str, notes: str | None = None) -> dict[str, Any]:
        normalized_decision = str(decision).strip().lower()
        if normalized_decision not in self.VALID_DECISIONS:
            return {"success": False, "status": "invalid_decision", "valid_decisions": sorted(self.VALID_DECISIONS)}

        report = await self.db.manual_live_pilot_reports.find_one({"user_id": user_id, "live_order_id": live_order_id}, {"_id": 0})
        if not report:
            return {"success": False, "status": "missing_report", "reason": "manual live pilot report not found", "live_order_id": live_order_id}
        if report.get("status") != "complete":
            return {"success": False, "status": "incomplete_report", "reason": "pilot report must be complete before signoff", "report_status": report.get("status")}

        requirement = await self.db.live_post_submit_reconciliation_requirements.find_one({"user_id": user_id, "live_order_id": live_order_id}, {"_id": 0})
        if requirement and requirement.get("status") == "pending":
            return {"success": False, "status": "pending_reconciliation", "reason": "post-submit reconciliation must be resolved before signoff"}

        now = self.utc_now()
        record = {
            "user_id": user_id,
            "live_order_id": live_order_id,
            "operator_id": operator_id,
            "decision": normalized_decision,
            "notes": notes,
            "report_hash": report.get("report_hash"),
            "signed_at": now,
        }
        record["signoff_hash"] = self.canonical_hash(record)
        await self.db.manual_live_pilot_signoffs.update_one(
            {"user_id": user_id, "live_order_id": live_order_id},
            {"$set": record, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return {"success": True, "status": "signed_off", "signoff": record}

    async def list_signoffs(self, user_id: str, limit: int = 100) -> dict[str, Any]:
        records = await self.db.manual_live_pilot_signoffs.find({"user_id": user_id}, {"_id": 0}).sort("signed_at", -1).limit(limit).to_list(limit)
        return {"user_id": user_id, "count": len(records), "signoffs": records}

    async def emit_unresolved_reconciliation_alerts(self, user_id: str) -> dict[str, Any]:
        unresolved = await self.unresolved_reconciliation_requirements(user_id)
        emitted = []
        for item in unresolved["pending"]:
            live_order_id = item.get("live_order_id")
            key = f"manual_live_unresolved_reconciliation:{user_id}:{live_order_id}:{item.get('created_at')}"
            existing = await self.db.alerts.find_one({"user_id": user_id, "type": "manual_live_unresolved_reconciliation", "context.key": key}, {"_id": 0})
            if existing:
                continue
            alert = await self.alerts.emit(
                user_id,
                "manual_live_unresolved_reconciliation",
                "critical",
                f"Manual live pilot reconciliation remains unresolved for order {live_order_id}",
                {"key": key, "requirement": item},
            )
            emitted.append(alert)
        return {"user_id": user_id, "pending_count": unresolved["pending_count"], "alerts_emitted": len(emitted), "checked_at": self.utc_now()}
