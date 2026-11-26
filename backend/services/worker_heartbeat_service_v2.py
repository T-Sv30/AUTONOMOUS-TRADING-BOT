import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from services.alert_service import AlertService


class WorkerHeartbeatServiceV2:
    """Worker heartbeat, lifecycle, and bot ownership registry.

    This service keeps the paper worker observable and restart-safe. It does not
    enable autonomous live trading.
    """

    DEFAULT_STALE_AFTER_SECONDS = 30
    DEFAULT_OWNERSHIP_TTL_SECONDS = 45
    SYSTEM_ALERT_USER_ID = "system"

    def __init__(self, db, worker_id: str | None = None):
        self.db = db
        self.worker_id = worker_id or os.getenv("WORKER_ID") or f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.hostname = socket.gethostname()

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None

    @classmethod
    def stale_after_seconds(cls) -> int:
        try:
            return int(os.getenv("WORKER_HEARTBEAT_STALE_AFTER_SECONDS", str(cls.DEFAULT_STALE_AFTER_SECONDS)))
        except ValueError:
            return cls.DEFAULT_STALE_AFTER_SECONDS

    @classmethod
    def ownership_ttl_seconds(cls) -> int:
        try:
            return int(os.getenv("BOT_OWNERSHIP_TTL_SECONDS", str(cls.DEFAULT_OWNERSHIP_TTL_SECONDS)))
        except ValueError:
            return cls.DEFAULT_OWNERSHIP_TTL_SECONDS

    async def beat(self, *, role: str, status: str = "running", active_bots: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self.utc_now()
        record = {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "role": role,
            "status": status,
            "active_bots": active_bots or [],
            "active_bot_count": len(active_bots or []),
            "metadata": metadata or {},
            "last_heartbeat_at": self.iso(now),
            "stale_after_seconds": self.stale_after_seconds(),
            "updated_at": self.iso(now),
        }
        await self.db.worker_heartbeats.update_one(
            {"worker_id": self.worker_id},
            {"$set": record, "$setOnInsert": {"started_at": self.iso(now)}},
            upsert=True,
        )
        return record

    async def mark_lifecycle(self, *, role: str, status: str, active_bots: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.beat(role=role, status=status, active_bots=active_bots, metadata=metadata)

    async def mark_stopped(self, *, role: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.beat(role=role, status="stopped", active_bots=[], metadata=metadata)

    @classmethod
    def is_stale(cls, record: dict[str, Any]) -> bool:
        last = cls.parse_datetime(record.get("last_heartbeat_at"))
        if not last:
            return True
        stale_after = int(record.get("stale_after_seconds") or cls.DEFAULT_STALE_AFTER_SECONDS)
        return datetime.now(UTC) - last > timedelta(seconds=stale_after)

    async def list_workers(self, limit: int = 100) -> dict[str, Any]:
        records = await self.db.worker_heartbeats.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(limit)
        for record in records:
            record["stale"] = self.is_stale(record)
        return {"workers": records, "stale_count": sum(1 for record in records if record.get("stale"))}

    async def stale_worker_report(self, limit: int = 100) -> dict[str, Any]:
        workers = (await self.list_workers(limit=limit))["workers"]
        stale = [worker for worker in workers if worker.get("stale") and worker.get("status") not in {"stopped", "stopping"}]
        return {"status": "ok" if not stale else "stale_workers_detected", "stale_workers": stale, "stale_count": len(stale), "checked_at": self.iso(self.utc_now())}

    async def emit_stale_worker_alerts(self, limit: int = 100) -> dict[str, Any]:
        report = await self.stale_worker_report(limit=limit)
        emitted = []
        for worker in report["stale_workers"]:
            key = f"stale_worker:{worker.get('worker_id')}:{worker.get('last_heartbeat_at')}"
            existing = await self.db.alerts.find_one({"user_id": self.SYSTEM_ALERT_USER_ID, "type": "worker_stale", "context.key": key}, {"_id": 0})
            if existing:
                continue
            alert = await AlertService(self.db).emit(
                self.SYSTEM_ALERT_USER_ID,
                "worker_stale",
                "warning",
                f"Worker {worker.get('worker_id')} heartbeat is stale",
                {"key": key, "worker": worker},
            )
            emitted.append(alert)
        return {**report, "alerts_emitted": len(emitted)}

    async def acquire_bot_ownership(self, user_id: str, ttl_seconds: int | None = None) -> bool:
        ttl_seconds = int(ttl_seconds or self.ownership_ttl_seconds())
        now = self.utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        existing = await self.db.bot_ownership.find_one({"user_id": user_id}, {"_id": 0})
        if existing:
            existing_expires = self.parse_datetime(existing.get("expires_at"))
            expired = existing_expires is None or existing_expires <= now
            if existing.get("worker_id") != self.worker_id and not expired:
                return False
        await self.db.bot_ownership.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "worker_id": self.worker_id,
                    "hostname": self.hostname,
                    "acquired_at": existing.get("acquired_at") if existing and existing.get("worker_id") == self.worker_id else self.iso(now),
                    "expires_at": self.iso(expires_at),
                    "updated_at": self.iso(now),
                    "ttl_seconds": ttl_seconds,
                }
            },
            upsert=True,
        )
        return True

    async def renew_bot_ownership(self, user_id: str, ttl_seconds: int | None = None) -> bool:
        ttl_seconds = int(ttl_seconds or self.ownership_ttl_seconds())
        now = self.utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        result = await self.db.bot_ownership.update_one(
            {"user_id": user_id, "worker_id": self.worker_id},
            {"$set": {"expires_at": self.iso(expires_at), "updated_at": self.iso(now), "ttl_seconds": ttl_seconds}},
        )
        return getattr(result, "modified_count", 0) == 1

    async def renew_active_ownerships(self, user_ids: list[str], ttl_seconds: int | None = None) -> dict[str, Any]:
        renewed = []
        failed = []
        for user_id in user_ids:
            if await self.renew_bot_ownership(user_id, ttl_seconds=ttl_seconds):
                renewed.append(user_id)
            else:
                failed.append(user_id)
        return {"renewed": renewed, "failed": failed, "renewed_count": len(renewed), "failed_count": len(failed)}

    async def release_bot_ownership(self, user_id: str) -> None:
        await self.db.bot_ownership.delete_one({"user_id": user_id, "worker_id": self.worker_id})

    async def release_all_owned_bots(self) -> int:
        result = await self.db.bot_ownership.delete_many({"worker_id": self.worker_id})
        return int(getattr(result, "deleted_count", 0) or 0)
