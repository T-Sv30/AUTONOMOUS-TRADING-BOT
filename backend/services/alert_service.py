from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument


class AlertService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    async def emit(
        self,
        user_id: str,
        alert_type: str,
        severity: str,
        message: str,
        context: dict[str, Any] | None = None,
        dedupe_seconds: int = 0,
    ) -> dict[str, Any]:
        """Insert an alert. With dedupe_seconds > 0, a matching open alert seen within that window is counted instead.

        A match has the same user, type and context symbol, is not acknowledged, and was last seen inside the window.
        """
        context = context or {}
        now = self.utc_now()
        if dedupe_seconds > 0:
            cutoff = (datetime.fromisoformat(now) - timedelta(seconds=dedupe_seconds)).isoformat()
            repeated = await self.db.alerts.find_one_and_update(
                {
                    "user_id": user_id,
                    "type": alert_type,
                    "context.symbol": context.get("symbol"),
                    "acknowledged": False,
                    "last_seen_at": {"$gte": cutoff},
                },
                {"$inc": {"count": 1}, "$set": {"last_seen_at": now, "severity": severity, "message": message, "context": context}},
                sort=[("last_seen_at", -1)],
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
            if repeated:
                repeated.pop("_id", None)
                return repeated
        alert = {
            "user_id": user_id,
            "type": alert_type,
            "severity": severity,
            "message": message,
            "context": context,
            "created_at": now,
            "last_seen_at": now,
            "count": 1,
            "acknowledged": False,
        }
        await self.db.alerts.insert_one(dict(alert))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
        return alert

    async def list_alerts(self, user_id: str, limit: int = 100):
        return await self.db.alerts.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
