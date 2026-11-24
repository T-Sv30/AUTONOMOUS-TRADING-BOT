from datetime import UTC, datetime
from typing import Any

from services.live_order_state_service_v2 import LiveOrderState, LiveOrderStateServiceV2
from services.live_pre_submit_safety_service_v2 import LivePreSubmitSafetyServiceV2
from services.live_risk_decision_service_v2 import LiveRiskDecisionServiceV2


class LiveManualOrderLifecycleServiceV2:
    """Lifecycle coordinator for manually gated live orders.

    This service wires the existing state machine, risk decision persistence, and
    post-submit reconciliation requirement into the manual live order flow. It
    does not place orders and does not enable autonomous live trading.
    """

    def __init__(self, db):
        self.db = db
        self.states = LiveOrderStateServiceV2(db)
        self.risk = LiveRiskDecisionServiceV2(db)
        self.pre_submit = LivePreSubmitSafetyServiceV2(db)

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def allowed_symbols(user_config: dict[str, Any]) -> list[str]:
        values = user_config.get("live_allowed_symbols") or user_config.get("allowed_symbols") or ["BTC-USD", "ETH-USD"]
        return [str(item).upper().strip() for item in values]

    @staticmethod
    def max_notional(user_config: dict[str, Any]) -> float:
        return float(user_config.get("live_max_order_notional_usd") or user_config.get("max_live_order_notional_usd") or 10.0)

    async def begin(self, *, user_id: str, symbol: str, side: str, notional_usd: float, base_units: float | None, client_order_id: str) -> dict[str, Any]:
        return await self.states.create_order(
            user_id=user_id,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            base_units=base_units,
            client_order_id=client_order_id,
        )

    async def gate_checked(self, live_order_id: str, gate: dict[str, Any]) -> dict[str, Any]:
        return await self.states.transition(
            order_id=live_order_id,
            next_state=LiveOrderState.GATE_CHECKED.value,
            reason="manual live gate checked",
            metadata={"gate_allowed": bool(gate.get("allowed")), "gate": gate},
        )

    async def risk_checked(self, *, user_id: str, symbol: str, side: str, notional_usd: float, user_config: dict[str, Any], live_order_id: str, dry_run: bool) -> dict[str, Any]:
        decision = await self.risk.allow_basic_manual_order(
            user_id=user_id,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            max_notional_usd=self.max_notional(user_config),
            allowed_symbols=self.allowed_symbols(user_config),
            metadata={"live_order_id": live_order_id, "dry_run": dry_run},
        )
        await self.states.transition(
            order_id=live_order_id,
            next_state=LiveOrderState.RISK_CHECKED.value,
            reason="manual live risk decision persisted",
            metadata={"risk_decision": decision},
        )
        return decision

    async def approval_recorded(self, *, live_order_id: str, dry_run: bool, approval_token: str | None) -> dict[str, Any]:
        if dry_run:
            await self.states.transition(
                order_id=live_order_id,
                next_state=LiveOrderState.APPROVAL_REQUIRED.value,
                reason="dry-run approval placeholder recorded",
                metadata={"dry_run": True},
            )
            return await self.states.transition(
                order_id=live_order_id,
                next_state=LiveOrderState.APPROVED.value,
                reason="dry-run approved for preview",
                metadata={"dry_run": True},
            )
        return await self.states.transition(
            order_id=live_order_id,
            next_state=LiveOrderState.APPROVED.value,
            reason="manual live approval accepted by gate",
            metadata={"approval_token_present": bool(approval_token)},
        )

    async def blocked(self, live_order_id: str, reason: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.FAILED.value, reason=reason, metadata=metadata)

    async def pre_submit_checked(self, *, user_id: str, live_order_id: str, dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"allowed": True, "dry_run": True, "checks": []}
        result = await self.pre_submit.safe_check(user_id)
        if not result.get("allowed"):
            await self.states.transition(
                order_id=live_order_id,
                next_state=LiveOrderState.HALTED.value,
                reason="manual live pre-submit safety blocked order",
                metadata={"pre_submit": result},
            )
        return result

    async def submitted(self, live_order_id: str, order: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        return await self.states.transition(
            order_id=live_order_id,
            next_state=LiveOrderState.SUBMITTED.value,
            reason="dry-run payload generated" if dry_run else "manual live order submitted",
            metadata={"order": order},
        )

    async def finalized_from_order(self, *, user_id: str, live_order_id: str, order: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        status = str(order.get("status") or "acknowledged")
        if dry_run:
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.ACKNOWLEDGED.value, reason="dry-run acknowledged", metadata={"order": order})
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.RECONCILIATION_PENDING.value, reason="dry-run reconciliation simulated", metadata={"order": order})
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.RECONCILED.value, reason="dry-run reconciled", metadata={"order": order})
            return {"required": False, "status": "dry_run_skipped"}
        if status == "filled":
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.ACKNOWLEDGED.value, reason="broker acknowledged order", metadata={"order": order})
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.FILLED.value, reason="broker reports filled", metadata={"order": order})
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.RECONCILIATION_PENDING.value, reason="post-submit reconciliation required", metadata={"order": order})
        elif status == "rejected":
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.REJECTED.value, reason="broker rejected order", metadata={"order": order})
        elif status == "canceled":
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.ACKNOWLEDGED.value, reason="broker acknowledged order", metadata={"order": order})
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.CANCELED.value, reason="broker canceled order", metadata={"order": order})
        else:
            await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.ACKNOWLEDGED.value, reason="broker acknowledged order", metadata={"order": order})
        return await self.reconciliation_required(user_id=user_id, live_order_id=live_order_id, order=order)

    async def adapter_error(self, live_order_id: str, error: str) -> dict[str, Any]:
        return await self.states.transition(order_id=live_order_id, next_state=LiveOrderState.FAILED.value, reason="adapter error during manual live order", metadata={"error": error})

    async def reconciliation_required(self, *, user_id: str, live_order_id: str, order: dict[str, Any]) -> dict[str, Any]:
        record = {
            "user_id": user_id,
            "live_order_id": live_order_id,
            "exchange_order_id": order.get("order_id"),
            "client_order_id": order.get("client_order_id"),
            "required": True,
            "status": "pending",
            "reason": "post-submit live-readonly reconciliation required before next non-dry-run live order",
            "created_at": self.utc_now(),
        }
        await self.db.live_post_submit_reconciliation_requirements.insert_one(record)
        return record
