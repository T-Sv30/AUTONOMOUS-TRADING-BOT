import asyncio
import hashlib
import json
import random
from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError


class LiveOrderAuditServiceV2:
    """Hash-chained live-order audit log.

    This service creates tamper-evident audit records for privileged live-order
    actions. MongoDB remains the storage layer, but every record includes:

    - previous_hash: hash of the immediately previous user audit event
    - payload_hash: hash of the canonical audit payload excluding chain fields
    - audit_hash: hash(previous_hash + payload_hash)

    The chain is per-user, which keeps verification cheap and isolates tenants.
    Each record also carries a hashed per-user ``sequence`` (1, 2, ...). The
    unique indexes on (user_id, sequence) and (user_id, previous_hash) make a
    concurrent append that read a stale latest record fail with
    DuplicateKeyError; append then re-reads and retries, so the chain cannot fork.
    """

    MAX_APPEND_RETRIES = 5
    VERIFY_BATCH_SIZE = 500
    CHAIN_FIELDS = frozenset({"previous_hash", "payload_hash", "audit_hash", "audit_version"})
    # Records written before sequence numbers sort first (missing sorts low), in created_at order.
    CHAIN_ORDER = [("sequence", 1), ("created_at", 1), ("_id", 1)]

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
            sort=[("sequence", -1), ("created_at", -1)],
        )

    def chain_record(self, record: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        previous_hash = previous.get("audit_hash") if previous else "GENESIS"
        sequence = int(previous.get("sequence") or 0) + 1 if previous else 1
        payload = {"created_at": self.utc_now(), **record, "sequence": sequence}
        payload_hash = self.sha256_payload(payload)
        audit_hash = hashlib.sha256(f"{previous_hash}:{payload_hash}".encode()).hexdigest()
        return {
            **payload,
            "previous_hash": previous_hash,
            "payload_hash": payload_hash,
            "audit_hash": audit_hash,
            "audit_version": "live_order_audit_v2",
        }

    async def append(self, record: dict[str, Any]) -> dict[str, Any]:
        user_id = record.get("user_id")
        if not user_id:
            raise ValueError("live order audit record requires user_id")

        for attempt in range(self.MAX_APPEND_RETRIES + 1):
            chained = self.chain_record(record, await self.latest_for_user(user_id))
            try:
                await self.db.live_order_audits.insert_one(dict(chained))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
                return chained
            except DuplicateKeyError:
                # Another append took this sequence or parent first. Re-read the latest record and retry.
                if attempt == self.MAX_APPEND_RETRIES:
                    raise
                # Random backoff spreads concurrent writers so they do not collide again on the next read.
                await asyncio.sleep(random.uniform(0, 0.02 * 2**attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    async def iter_user_chain(self, user_id: str):
        """Every record of the user's chain in order, read in pages of VERIFY_BATCH_SIZE, with no cap."""
        offset = 0
        while True:
            page = (
                await self.db.live_order_audits.find({"user_id": user_id}, {"_id": 0})
                .sort(self.CHAIN_ORDER)
                .skip(offset)
                .limit(self.VERIFY_BATCH_SIZE)
                .to_list(self.VERIFY_BATCH_SIZE)
            )
            for record in page:
                yield record
            if len(page) < self.VERIFY_BATCH_SIZE:
                return
            offset += len(page)

    async def verify_user_chain(self, user_id: str) -> dict[str, Any]:
        expected_previous = "GENESIS"
        issues = []
        checked = 0

        async for record in self.iter_user_chain(user_id):
            index = checked
            checked += 1
            payload = {key: value for key, value in record.items() if key not in self.CHAIN_FIELDS}
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
            "checked_records": checked,
            "issues": issues,
            "checked_at": self.utc_now(),
        }
