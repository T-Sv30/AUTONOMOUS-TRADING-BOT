from datetime import UTC, datetime
from typing import Any


class LiveRiskDecisionServiceV2:
    """Persisted pre-submit risk decisions for manual live orders.

    This service does not replace the existing live gate. It records a durable,
    auditable risk decision object before live submission can proceed.
    """

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    async def decide(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        notional_usd: float,
        request_id: str = "",
        checks: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checks = checks or []
        blocked = [check for check in checks if not check.get("passed", False)]
        decision = {
            "user_id": user_id,
            "symbol": str(symbol).upper().strip(),
            "side": str(side).upper().strip(),
            "notional_usd": round(float(notional_usd), 8),
            "decision": "block" if blocked else "allow",
            "reason": blocked[0].get("reason") if blocked else "all live risk checks passed",
            "checks": checks,
            "metadata": metadata or {},
            "request_id": request_id,
            "created_at": self.utc_now(),
            "version": "live_risk_decision_v2",
        }
        await self.db.live_risk_decisions.insert_one(decision)
        return decision

    async def allow_basic_manual_order(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        notional_usd: float,
        max_notional_usd: float,
        allowed_symbols: list[str],
        request_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checks = [
            {"name": "positive_notional", "passed": float(notional_usd) > 0, "reason": "order notional must be positive"},
            {"name": "max_notional", "passed": float(notional_usd) <= float(max_notional_usd), "reason": "order exceeds live max notional"},
            {"name": "symbol_allowlist", "passed": str(symbol).upper().strip() in {item.upper().strip() for item in allowed_symbols}, "reason": "symbol is not allowed for live trading"},
        ]
        return await self.decide(
            user_id=user_id,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            checks=checks,
            request_id=request_id,
            metadata=metadata,
        )
