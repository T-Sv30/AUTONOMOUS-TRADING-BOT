from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class ExecutionControlV2:
    def __init__(self, db, lock_ttl_seconds: int = 30):
        self.db = db
        self.lock_ttl_seconds = lock_ttl_seconds

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def build_idempotency_key(user_id: str, symbol: str, side: str, signal_snapshot: dict[str, Any] | None = None) -> str:
        """One key per decision: the same strategy may trade again on a later closed candle."""
        signal_snapshot = signal_snapshot or {}
        strategy = signal_snapshot.get("selected", {}).get("strategy") or signal_snapshot.get("strategy", "unknown")
        action = signal_snapshot.get("action", side)
        return f"{user_id}:{symbol}:{side}:{action}:{strategy}"  # THROWAWAY: old key, no timeframe or decision time

    async def acquire_lock(self, user_id: str, symbol: str, side: str) -> dict[str, Any]:
        key = f"{user_id}:{symbol}:{side}"
        now = self.utc_now()
        now_iso = now.isoformat()
        expires_at = now + timedelta(seconds=self.lock_ttl_seconds)
        expires_at_iso = expires_at.isoformat()

        try:
            acquired = await self.db.execution_locks.find_one_and_update(
                {
                    "key": key,
                    "$or": [
                        {"expires_at": {"$lte": now_iso}},
                        {"expires_at": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "key": key,
                        "user_id": user_id,
                        "symbol": symbol,
                        "side": side,
                        "expires_at": expires_at_iso,
                        "updated_at": now_iso,
                    },
                    "$setOnInsert": {"created_at": now_iso},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )
        except DuplicateKeyError:
            return {"acquired": False, "key": key, "reason": "execution lock active"}

        if not acquired:
            return {"acquired": False, "key": key, "reason": "execution lock active"}

        if acquired.get("updated_at") != now_iso:
            return {"acquired": False, "key": key, "reason": "execution lock active"}

        return {"acquired": True, "key": key, "expires_at": expires_at_iso}

    async def release_lock(self, key: str) -> None:
        await self.db.execution_locks.delete_one({"key": key})

    async def already_executed(self, idempotency_key: str) -> bool:
        # Any attempt that was not rejected may have reached the venue, so it blocks a retry.
        existing = await self.db.trades_v2.find_one({"idempotency_key": idempotency_key, "status": {"$ne": "rejected"}}, {"_id": 0})
        return existing is not None
