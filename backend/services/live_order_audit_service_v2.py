import hashlib
import json
from datetime import UTC, datetime
from typing import Any


class LiveOrderAuditServiceV2:
    """Hash-chained live-order audit log.

    This service creates tamper-evident audit records for privileged live-order
    actions. MongoDB remains the storage layer, but every record includes:

    - previous_hash: hash of the immediately previous user audit event
    - payload_hash: hash of the canonical audit payload excluding chain fields
    - audit_hash: hash(previous_hash + payload_hash)

    The chain is per-user, which keeps verification cheap and isolates tenants.
    """

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def canonical_json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def sha256_payload(cls, payload: dict[str, Any]) -> str:
        return hashlib.sha256(cls.canonical_json(payload).encode("utf-8")).hexdigest()

    async def latest_for_user(self, user_id: str) -> dict[str, Any] | None:
        return await self.db.live_order_audits.find_one(
            {"user_id": user_id},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    async def append(self, record: dict[str, Any]) -> dict[str, Any]:
        user_id = record.get("user_id")
        if not user_id:
            raise ValueError("live order audit record requires user_id")

        previous = await self.latest_for_user(user_id)
        previous_hash = previous.get("audit_hash") if previous else "GENESIS"
        payload = {"created_at": self.utc_now(), **record}
        payload_hash = self.sha256_payload(payload)
        audit_hash = hashlib.sha256(f"{previous_hash}:{payload_hash}".encode()).hexdigest()
        chained = {
            **payload,
            "previous_hash": previous_hash,
            "payload_hash": payload_hash,
            "audit_hash": audit_hash,
            "audit_version": "live_order_audit_v2",
        }
        await self.db.live_order_audits.insert_one(chained)
        return chained

    async def verify_user_chain(self, user_id: str, limit: int = 1000) -> dict[str, Any]:
        records = await self.db.live_order_audits.find({"user_id": user_id}, {"_id": 0}).sort("created_at", 1).limit(limit).to_list(limit)
        expected_previous = "GENESIS"
        issues = []

        for index, record in enumerate(records):
            chain_fields = {"previous_hash", "payload_hash", "audit_hash", "audit_version"}
            payload = {key: value for key, value in record.items() if key not in chain_fields}
            payload_hash = self.sha256_payload(payload)
            audit_hash = hashlib.sha256(f"{expected_previous}:{payload_hash}".encode()).hexdigest()

            if record.get("previous_hash") != expected_previous:
                issues.append({"index": index, "type": "previous_hash_mismatch"})
            if record.get("payload_hash") != payload_hash:
                issues.append({"index": index, "type": "payload_hash_mismatch"})
            if record.get("audit_hash") != audit_hash:
                issues.append({"index": index, "type": "audit_hash_mismatch"})

            expected_previous = record.get("audit_hash") or "BROKEN"

        return {
            "user_id": user_id,
            "status": "ok" if not issues else "tamper_detected",
            "checked_records": len(records),
            "issues": issues,
            "checked_at": self.utc_now(),
        }
