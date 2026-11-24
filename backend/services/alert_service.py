from datetime import UTC, datetime
from typing import Any


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
    ) -> dict[str, Any]:
        alert = {
            "user_id": user_id,
            "type": alert_type,
            "severity": severity,
            "message": message,
            "context": context or {},
            "created_at": self.utc_now(),
            "acknowledged": False,
        }
        await self.db.alerts.insert_one(alert)
        return alert

    async def list_alerts(self, user_id: str, limit: int = 100):
        return await self.db.alerts.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
