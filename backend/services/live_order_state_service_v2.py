import hashlib
import json
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class LiveOrderStateError(RuntimeError):
    pass


class LiveOrderState(str, Enum):
    REQUESTED = "requested"
    GATE_CHECKED = "gate_checked"
    RISK_CHECKED = "risk_checked"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"
    RECONCILIATION_PENDING = "reconciliation_pending"
    RECONCILED = "reconciled"
    FAILED = "failed"
    HALTED = "halted"


TERMINAL_STATES = {
    LiveOrderState.RECONCILED.value,
    LiveOrderState.REJECTED.value,
    LiveOrderState.CANCELED.value,
    LiveOrderState.FAILED.value,
    LiveOrderState.HALTED.value,
}

UNRESOLVED_STATES = {state.value for state in LiveOrderState} - TERMINAL_STATES

ALLOWED_TRANSITIONS = {
    LiveOrderState.REQUESTED.value: {LiveOrderState.GATE_CHECKED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.GATE_CHECKED.value: {LiveOrderState.RISK_CHECKED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.RISK_CHECKED.value: {LiveOrderState.APPROVAL_REQUIRED.value, LiveOrderState.APPROVED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.APPROVAL_REQUIRED.value: {LiveOrderState.APPROVED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.APPROVED.value: {LiveOrderState.SUBMITTED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.SUBMITTED.value: {LiveOrderState.ACKNOWLEDGED.value, LiveOrderState.REJECTED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.ACKNOWLEDGED.value: {LiveOrderState.PARTIALLY_FILLED.value, LiveOrderState.FILLED.value, LiveOrderState.REJECTED.value, LiveOrderState.CANCELED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.PARTIALLY_FILLED.value: {LiveOrderState.FILLED.value, LiveOrderState.CANCELED.value, LiveOrderState.RECONCILIATION_PENDING.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.FILLED.value: {LiveOrderState.RECONCILIATION_PENDING.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.RECONCILIATION_PENDING.value: {LiveOrderState.RECONCILED.value, LiveOrderState.FAILED.value, LiveOrderState.HALTED.value},
    LiveOrderState.REJECTED.value: set(),
    LiveOrderState.CANCELED.value: set(),
    LiveOrderState.RECONCILED.value: set(),
    LiveOrderState.FAILED.value: set(),
    LiveOrderState.HALTED.value: set(),
}


class LiveOrderStateServiceV2:
    """Hash-chained live order state machine.

    Every live order transition is immutable and chained per order. This service
    is intentionally separate from exchange adapters so it can also track orders
    blocked before submission.
    """

    VERSION = "live_order_state_v2"

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

    @staticmethod
    def new_order_id() -> str:
        return f"live_order_{secrets.token_urlsafe(18)}"

    async def latest_transition(self, order_id: str) -> dict[str, Any] | None:
        return await self.db.live_order_transitions.find_one({"order_id": order_id}, {"_id": 0}, sort=[("sequence", -1)])

    async def latest_unresolved_for_user(self, user_id: str) -> dict[str, Any] | None:
        return await self.db.live_order_transitions.find_one(
            {"user_id": user_id, "is_latest": True, "state": {"$in": sorted(UNRESOLVED_STATES)}},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    async def has_unresolved_for_user(self, user_id: str) -> bool:
        return bool(await self.latest_unresolved_for_user(user_id))

    async def create_order(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        notional_usd: float | None = None,
        base_units: float | None = None,
        client_order_id: str | None = None,
        request_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        order_id = self.new_order_id()
        payload = {
            "order_id": order_id,
            "user_id": user_id,
            "symbol": str(symbol).upper().strip(),
            "side": str(side).upper().strip(),
            "notional_usd": round(float(notional_usd), 8) if notional_usd is not None else None,
            "base_units": round(float(base_units), 12) if base_units is not None else None,
            "client_order_id": client_order_id,
            "state": LiveOrderState.REQUESTED.value,
            "previous_state": None,
            "sequence": 1,
            "request_id": request_id,
            "reason": "live order requested",
            "metadata": metadata or {},
            "created_at": self.utc_now(),
            "is_latest": True,
            "version": self.VERSION,
        }
        payload_hash = self.sha256_payload(payload)
        transition_hash = hashlib.sha256(f"GENESIS:{payload_hash}".encode()).hexdigest()
        record = {**payload, "previous_hash": "GENESIS", "payload_hash": payload_hash, "transition_hash": transition_hash}
        await self.db.live_order_transitions.insert_one(dict(record))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
        return record

    async def transition(
        self,
        *,
        order_id: str,
        next_state: str,
        reason: str,
        request_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest = await self.latest_transition(order_id)
        if not latest:
            raise LiveOrderStateError("live order not found")
        current_state = latest["state"]
        next_state = str(next_state).strip()
        if next_state not in ALLOWED_TRANSITIONS.get(current_state, set()):
            raise LiveOrderStateError(f"invalid live order transition: {current_state} -> {next_state}")
        await self.db.live_order_transitions.update_many({"order_id": order_id, "is_latest": True}, {"$set": {"is_latest": False}})
        payload = {
            "order_id": order_id,
            "user_id": latest["user_id"],
            "symbol": latest["symbol"],
            "side": latest["side"],
            "notional_usd": latest.get("notional_usd"),
            "base_units": latest.get("base_units"),
            "client_order_id": latest.get("client_order_id"),
            "state": next_state,
            "previous_state": current_state,
            "sequence": int(latest.get("sequence", 1)) + 1,
            "request_id": request_id,
            "reason": reason,
            "metadata": metadata or {},
            "created_at": self.utc_now(),
            "is_latest": True,
            "version": self.VERSION,
        }
        payload_hash = self.sha256_payload(payload)
        previous_hash = latest["transition_hash"]
        transition_hash = hashlib.sha256(f"{previous_hash}:{payload_hash}".encode()).hexdigest()
        record = {**payload, "previous_hash": previous_hash, "payload_hash": payload_hash, "transition_hash": transition_hash}
        await self.db.live_order_transitions.insert_one(dict(record))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
        return record

    async def transition_many(self, order_id: str, states: Iterable[str], *, reason: str, request_id: str = "", metadata: dict[str, Any] | None = None):
        latest = None
        for state in states:
            latest = await self.transition(order_id=order_id, next_state=state, reason=reason, request_id=request_id, metadata=metadata)
        return latest

    async def verify_chain(self, order_id: str, limit: int = 1000) -> dict[str, Any]:
        records = await self.db.live_order_transitions.find({"order_id": order_id}, {"_id": 0}).sort("sequence", 1).limit(limit).to_list(limit)
        expected_previous = "GENESIS"
        issues = []
        previous_state = None
        for index, record in enumerate(records):
            chain_fields = {"previous_hash", "payload_hash", "transition_hash"}
            payload = {key: value for key, value in record.items() if key not in chain_fields}
            if "is_latest" in payload:
                # Every record is hashed with is_latest=True at write time; the flag is a mutable
                # index that later transitions flip to False, so verify against the written value.
                payload["is_latest"] = True
            payload_hash = self.sha256_payload(payload)
            transition_hash = hashlib.sha256(f"{expected_previous}:{payload_hash}".encode()).hexdigest()
            if record.get("previous_hash") != expected_previous:
                issues.append({"index": index, "type": "previous_hash_mismatch"})
            if record.get("payload_hash") != payload_hash:
                issues.append({"index": index, "type": "payload_hash_mismatch"})
            if record.get("transition_hash") != transition_hash:
                issues.append({"index": index, "type": "transition_hash_mismatch"})
            if previous_state is not None and record.get("state") not in ALLOWED_TRANSITIONS.get(previous_state, set()):
                issues.append({"index": index, "type": "invalid_transition", "from": previous_state, "to": record.get("state")})
            previous_state = record.get("state")
            expected_previous = record.get("transition_hash") or "BROKEN"
        return {"order_id": order_id, "status": "ok" if not issues else "tamper_detected", "checked_records": len(records), "issues": issues, "checked_at": self.utc_now()}
