import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from services.live_readonly_service_v2 import LiveReadonlyServiceV2
from services.live_trading_service_v2 import LiveTradingServiceV2


class ManualLivePilotWorkflowServiceV2:
    """Post-submit workflow for tiny human-approved manual live pilots.

    This service does not submit live orders. It supports operator visibility,
    reconciliation resolution, and immutable pilot reporting after a manual live
    order path has already created lifecycle/audit/reconciliation records.
    """

    def __init__(self, db):
        self.db = db
        self.live_readonly = LiveReadonlyServiceV2(db)
        self.live_trading = LiveTradingServiceV2(db)

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def canonical_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

    async def pending_reconciliation_requirements(self, user_id: str, limit: int = 100) -> dict[str, Any]:
        pending = await self.db.live_post_submit_reconciliation_requirements.find({"user_id": user_id, "status": "pending"}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
        return {"user_id": user_id, "pending_count": len(pending), "pending": pending, "checked_at": self.utc_now()}

    async def resolve_reconciliation_requirement(self, *, user_id: str, live_order_id: str, resolution: str, notes: str | None = None) -> dict[str, Any]:
        existing = await self.db.live_post_submit_reconciliation_requirements.find_one({"user_id": user_id, "live_order_id": live_order_id}, {"_id": 0})
        if not existing:
            return {"success": False, "status": "missing", "reason": "post-submit reconciliation requirement not found", "live_order_id": live_order_id}
        if existing.get("status") != "pending":
            return {"success": True, "status": existing.get("status"), "already_resolved": True, "requirement": existing}

        reconciliation = await self.live_readonly.latest_snapshot_status(user_id)
        latest_report = await self.db.live_readonly_reports.find_one({"user_id": user_id}, {"_id": 0}, sort=[("checked_at", -1)])
        now = self.utc_now()
        update = {
            "status": "resolved",
            "resolution": resolution,
            "notes": notes,
            "resolved_at": now,
            "latest_snapshot_status": reconciliation,
            "latest_reconciliation_report": latest_report,
        }
        await self.db.live_post_submit_reconciliation_requirements.update_one({"user_id": user_id, "live_order_id": live_order_id}, {"$set": update})
        resolved = {**existing, **update}
        return {"success": True, "status": "resolved", "live_order_id": live_order_id, "requirement": resolved}

    async def build_pilot_report(self, user_id: str, live_order_id: str) -> dict[str, Any]:
        transitions = await self.db.live_order_transitions.find({"user_id": user_id, "order_id": live_order_id}, {"_id": 0}).sort("sequence", 1).limit(200).to_list(200)
        requirement = await self.db.live_post_submit_reconciliation_requirements.find_one({"user_id": user_id, "live_order_id": live_order_id}, {"_id": 0})
        audits = await self.db.live_order_audits.find({"user_id": user_id, "live_order_id": live_order_id}, {"_id": 0}).sort("created_at", 1).limit(100).to_list(100)
        latest_readonly_status = await self.live_readonly.latest_snapshot_status(user_id)
        latest_reconciliation_report = await self.db.live_readonly_reports.find_one({"user_id": user_id}, {"_id": 0}, sort=[("checked_at", -1)])
        audit_chain = await self.live_trading.verify_audit_chain(user_id)

        report = {
            "user_id": user_id,
            "live_order_id": live_order_id,
            "status": "complete" if requirement and requirement.get("status") == "resolved" else "incomplete",
            "generated_at": self.utc_now(),
            "transition_count": len(transitions),
            "transitions": transitions,
            "post_submit_reconciliation_requirement": requirement,
            "audit_count": len(audits),
            "audits": audits,
            "latest_live_readonly_status": latest_readonly_status,
            "latest_reconciliation_report": latest_reconciliation_report,
            "audit_chain": audit_chain,
        }
        report["report_hash"] = self.canonical_hash(report)
        await self.db.manual_live_pilot_reports.update_one(
            {"user_id": user_id, "live_order_id": live_order_id},
            {"$set": report, "$setOnInsert": {"created_at": report["generated_at"]}},
            upsert=True,
        )
        return report

    async def list_pilot_reports(self, user_id: str, limit: int = 100) -> dict[str, Any]:
        reports = await self.db.manual_live_pilot_reports.find({"user_id": user_id}, {"_id": 0}).sort("generated_at", -1).limit(limit).to_list(limit)
        return {"user_id": user_id, "reports": reports, "count": len(reports)}
