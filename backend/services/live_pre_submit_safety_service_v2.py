import os
from datetime import UTC, datetime
from typing import Any

from services.live_order_state_service_v2 import LiveOrderStateServiceV2


class LivePreSubmitSafetyError(RuntimeError):
    pass


class LivePreSubmitSafetyServiceV2:
    """Fail-closed pre-submit checks for non-dry-run live orders."""

    def __init__(self, db):
        self.db = db
        self.orders = LiveOrderStateServiceV2(db)

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def max_reconciliation_age_seconds() -> int:
        try:
            return int(os.getenv("LIVE_MAX_RECONCILIATION_AGE_SECONDS", "300"))
        except ValueError:
            return 300

    async def halt_record(self, user_id: str) -> dict[str, Any] | None:
        return await self.db.live_halts.find_one(
            {"$or": [{"scope": "global"}, {"scope": "user", "user_id": user_id}], "active": True},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    async def latest_reconciliation(self, user_id: str) -> dict[str, Any] | None:
        return await self.db.live_readonly_reports.find_one({"user_id": user_id}, {"_id": 0}, sort=[("created_at", -1), ("snapshot.timestamp", -1)])

    @staticmethod
    def parse_timestamp(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            return None

    async def check(self, user_id: str) -> dict[str, Any]:
        checks = []

        async def add(name: str, passed: bool, reason: str, metadata: dict[str, Any] | None = None):
            checks.append({"name": name, "passed": passed, "reason": reason, "metadata": metadata or {}})
            if not passed:
                raise LivePreSubmitSafetyError(reason)

        halt = await self.halt_record(user_id)
        await add("no_active_halt", not bool(halt), "active live trading halt blocks order submission", {"halt": halt or {}})

        unresolved = await self.orders.latest_unresolved_for_user(user_id)
        await add("no_unresolved_live_order", not bool(unresolved), "unresolved live order blocks new live order submission", {"order": unresolved or {}})

        require_fresh = self.env_bool("LIVE_REQUIRE_FRESH_RECONCILIATION", True)
        if require_fresh:
            report = await self.latest_reconciliation(user_id)
            timestamp = None
            if report:
                timestamp = report.get("checked_at") or report.get("snapshot", {}).get("timestamp") or report.get("generated_at")
            parsed = self.parse_timestamp(timestamp)
            age = (self.utc_now() - parsed).total_seconds() if parsed else None
            max_age = self.max_reconciliation_age_seconds()
            await add(
                "fresh_reconciliation",
                bool(parsed and age is not None and age <= max_age),
                "fresh live-readonly reconciliation required before non-dry-run live order",
                {"age_seconds": age, "max_age_seconds": max_age},
            )
        else:
            checks.append({"name": "fresh_reconciliation", "passed": True, "reason": "fresh reconciliation not required by config", "metadata": {}})

        return {"allowed": True, "checks": checks}

    async def safe_check(self, user_id: str) -> dict[str, Any]:
        try:
            return await self.check(user_id)
        except LivePreSubmitSafetyError as exc:
            return {"allowed": False, "reason": str(exc)}

    HALT_SCOPES = ("user", "global")

    async def create_halt(self, *, user_id: str | None, scope: str, reason: str, created_by: str = "system") -> dict[str, Any]:
        record = {"scope": scope, "user_id": user_id, "reason": reason, "active": True, "created_by": created_by, "created_at": self.utc_now().isoformat()}
        await self.db.live_halts.insert_one(dict(record))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
        return record

    async def release_halts(self, *, user_id: str | None, scope: str, released_by: str, reason: str | None = None) -> int:
        """Deactivate active halts of one scope, optionally only those with this exact reason.

        Returns how many were released.
        """
        if scope not in self.HALT_SCOPES:
            raise ValueError(f"halt scope must be one of {self.HALT_SCOPES}")
        query: dict[str, Any] = {"scope": scope, "active": True}
        if scope == "user":
            if not user_id:
                raise ValueError("user_id is required to release a user halt")
            query["user_id"] = user_id
        if reason is not None:
            query["reason"] = reason
        result = await self.db.live_halts.update_many(
            query,
            {"$set": {"active": False, "released_at": self.utc_now().isoformat(), "released_by": released_by}},
        )
        return int(getattr(result, "modified_count", 0) or 0)
