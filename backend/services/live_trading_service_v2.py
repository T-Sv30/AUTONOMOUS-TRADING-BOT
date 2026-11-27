from datetime import UTC, datetime
from typing import Any

from services.alert_service import AlertService
from services.coinbase_live_execution_adapter_v2 import (
    CoinbaseLiveExecutionAdapterV2,
    CoinbaseLiveExecutionError,
    CoinbaseLiveOrderUnknownOutcome,
)
from services.execution_service_v2 import ExecutionServiceV2
from services.live_approval_challenge_service_v2 import (
    LiveApprovalChallengeError,
    LiveApprovalChallengeServiceV2,
)
from services.live_manual_order_lifecycle_service_v2 import (
    LiveManualOrderLifecycleServiceV2,
)
from services.live_order_audit_service_v2 import LiveOrderAuditServiceV2
from services.live_order_state_service_v2 import LiveOrderState
from services.live_submit_policy_v2 import LiveSubmitPolicyV2
from services.live_trading_gate_v2 import LiveTradingGateV2
from services.trading_mode_v2 import TradingModeService


class LiveTradingServiceV2:
    """Gated manual live execution orchestration.

    This service is the manual live order entrypoint. It keeps the existing live
    gate and adapter boundaries, while wiring in:

    - signed approval challenges, verified and consumed here for every
      non-dry-run order before the gate runs;
    - live order lifecycle state transitions;
    - persisted live risk decisions;
    - pre-submit safety checks;
    - post-submit reconciliation requirements;
    - hash-chained live audit records.

    It does not enable autonomous live trading.
    """

    def __init__(
        self,
        db,
        adapter: CoinbaseLiveExecutionAdapterV2 | None = None,
        gate: LiveTradingGateV2 | None = None,
    ):
        self.db = db
        self.adapter = adapter or CoinbaseLiveExecutionAdapterV2()
        self.gate = gate or LiveTradingGateV2()
        self.audits = LiveOrderAuditServiceV2(db)
        self.lifecycle = LiveManualOrderLifecycleServiceV2(db)

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    async def _user_config(self, user_id: str) -> dict[str, Any]:
        return await self.db.bot_configs.find_one({"user_id": user_id}, {"_id": 0}) or {}

    async def _audit(self, record: dict[str, Any]) -> dict[str, Any]:
        return await self.audits.append(record)

    async def _verify_approval(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        notional_usd: float,
        base_units: float | None,
        reference_price: float | None,
        approval_token: str | None,
    ) -> dict[str, Any]:
        challenges = LiveApprovalChallengeServiceV2(self.db)
        if side == "BUY":
            return await challenges.verify_and_consume(
                user_id=user_id,
                approval_token=approval_token,
                side="BUY",
                symbol=symbol,
                notional_usd=notional_usd,
            )
        return await challenges.verify_and_consume(
            user_id=user_id,
            approval_token=approval_token,
            side="SELL",
            symbol=symbol,
            base_units=base_units,
            reference_price=reference_price,
        )

    async def preview_market_buy(
        self,
        user_id: str,
        symbol: str,
        notional_usd: float,
    ) -> dict[str, Any]:
        return await self.place_market_buy(
            user_id=user_id,
            symbol=symbol,
            notional_usd=notional_usd,
            dry_run=True,
        )

    async def place_market_buy(
        self,
        user_id: str,
        symbol: str,
        notional_usd: float,
        approval_token: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return await self._place_manual_order(
            user_id=user_id,
            symbol=symbol,
            side="BUY",
            notional_usd=float(notional_usd),
            base_units=None,
            reference_price=None,
            approval_token=approval_token,
            dry_run=dry_run,
        )

    async def preview_market_sell(
        self,
        user_id: str,
        symbol: str,
        base_units: float,
        reference_price: float,
    ) -> dict[str, Any]:
        return await self.place_market_sell(
            user_id=user_id,
            symbol=symbol,
            base_units=base_units,
            reference_price=reference_price,
            dry_run=True,
        )

    async def place_market_sell(
        self,
        user_id: str,
        symbol: str,
        base_units: float,
        reference_price: float,
        approval_token: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        notional_usd = float(base_units) * float(reference_price)
        return await self._place_manual_order(
            user_id=user_id,
            symbol=symbol,
            side="SELL",
            notional_usd=notional_usd,
            base_units=float(base_units),
            reference_price=float(reference_price),
            approval_token=approval_token,
            dry_run=dry_run,
        )

    async def _place_manual_order(
        self,
        *,
        user_id: str,
        symbol: str,
        side: str,
        notional_usd: float,
        base_units: float | None,
        reference_price: float | None,
        approval_token: str | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        user_config = await self._user_config(user_id)
        mode = TradingModeService().mode.value

        client_order_id = ExecutionServiceV2.build_client_order_id(
            f"manual_{side.lower()}",
            symbol,
            f"{user_id}:{symbol}:{side}:{self.utc_now()}",
        )

        lifecycle_order = await self.lifecycle.begin(
            user_id=user_id,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            base_units=base_units,
            client_order_id=client_order_id,
        )
        live_order_id = lifecycle_order["order_id"]
        order_fields = {
            "user_id": user_id,
            "symbol": symbol,
            "side": side,
            "notional_usd": round(float(notional_usd), 8),
            "base_units": round(float(base_units), 12) if base_units is not None else None,
            "reference_price": reference_price,
            "client_order_id": client_order_id,
            "live_order_id": live_order_id,
            "dry_run": dry_run,
        }

        if not dry_run:
            try:
                await self._verify_approval(
                    user_id=user_id,
                    symbol=symbol,
                    side=side,
                    notional_usd=notional_usd,
                    base_units=base_units,
                    reference_price=reference_price,
                    approval_token=approval_token,
                )
            except LiveApprovalChallengeError as exc:
                await self.lifecycle.blocked(
                    live_order_id,
                    "manual live order approval challenge rejected",
                    {"approval_error": str(exc)},
                )
                audit = await self._audit({**order_fields, "status": "approval_rejected", "reason": str(exc)})
                return {
                    "success": False,
                    "status": "approval_rejected",
                    "live_order_id": live_order_id,
                    "reason": str(exc),
                    "audit": audit,
                }

        gate = self.gate.safe_preflight(
            trading_mode=mode,
            user_config=user_config,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            approval_token=approval_token,
            dry_run=dry_run,
        )
        await self.lifecycle.gate_checked(live_order_id, gate)

        risk_decision = await self.lifecycle.risk_checked(
            user_id=user_id,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            user_config=user_config,
            live_order_id=live_order_id,
            dry_run=dry_run,
        )

        audit = await self._audit(
            {
                **order_fields,
                "gate": gate,
                "risk_decision": risk_decision,
                "status": (
                    "preflight_passed"
                    if gate.get("allowed") and risk_decision.get("decision") == "allow"
                    else "blocked"
                ),
            }
        )

        if not gate.get("allowed") or risk_decision.get("decision") != "allow":
            await self.lifecycle.blocked(
                live_order_id,
                "manual live order blocked by gate or risk",
                {"gate": gate, "risk_decision": risk_decision},
            )
            return {
                "success": False,
                "status": "blocked",
                "live_order_id": live_order_id,
                "audit": audit,
                "gate": gate,
                "risk_decision": risk_decision,
            }

        await self.lifecycle.approval_recorded(
            live_order_id=live_order_id,
            dry_run=dry_run,
            approval_token=approval_token,
        )

        pre_submit = await self.lifecycle.pre_submit_checked(
            user_id=user_id,
            live_order_id=live_order_id,
            dry_run=dry_run,
        )
        if not pre_submit.get("allowed"):
            await self._audit(
                {
                    **audit,
                    "status": "pre_submit_blocked",
                    "pre_submit": pre_submit,
                }
            )
            return {
                "success": False,
                "status": "pre_submit_blocked",
                "live_order_id": live_order_id,
                "audit": audit,
                "gate": gate,
                "risk_decision": risk_decision,
                "pre_submit": pre_submit,
            }

        try:
            if side == "BUY":
                order = await self.adapter.place_market_buy(
                    symbol,
                    notional_usd,
                    client_order_id=client_order_id,
                    dry_run=dry_run,
                )
            else:
                order = await self.adapter.place_market_sell(
                    symbol,
                    float(base_units or 0.0),
                    client_order_id=client_order_id,
                    dry_run=dry_run,
                )
        except CoinbaseLiveOrderUnknownOutcome as exc:
            return await self._record_unknown_outcome(
                user_id=user_id,
                live_order_id=live_order_id,
                client_order_id=client_order_id,
                audit=audit,
                error=str(exc),
            )
        except CoinbaseLiveExecutionError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                await self.lifecycle.rejected(live_order_id, str(exc))
                status = "rejected"
            else:
                await self.lifecycle.adapter_error(live_order_id, str(exc))
                status = "adapter_error"
            await self._audit(
                {
                    **audit,
                    "status": status,
                    "error": str(exc),
                }
            )
            raise

        await self.lifecycle.submitted(live_order_id, order, dry_run)

        reconciliation_requirement = await self.lifecycle.finalized_from_order(
            user_id=user_id,
            live_order_id=live_order_id,
            order=order,
            dry_run=dry_run,
        )

        await self._audit(
            {
                **audit,
                "status": order.get("status"),
                "order": order,
                "reconciliation_requirement": reconciliation_requirement,
            }
        )

        return {
            "success": bool(order.get("success")),
            "status": order.get("status"),
            "live_order_id": live_order_id,
            "order": order,
            "audit": audit,
            "gate": gate,
            "risk_decision": risk_decision,
            "reconciliation_requirement": reconciliation_requirement,
        }

    @staticmethod
    def unknown_outcome_halt_reason(live_order_id: str) -> str:
        return f"unknown order outcome {live_order_id}"

    async def _record_unknown_outcome(
        self,
        *,
        user_id: str,
        live_order_id: str,
        client_order_id: str,
        audit: dict[str, Any],
        error: str,
    ) -> dict[str, Any]:
        # Halt first: it is what keeps the next order away from the exchange.
        halt = await self.lifecycle.pre_submit.create_halt(
            user_id=user_id,
            scope="user",
            reason=self.unknown_outcome_halt_reason(live_order_id),
            created_by="live_trading_service",
        )
        await self.lifecycle.submit_unknown(live_order_id, error)
        recovery = LiveSubmitPolicyV2.recovery_action_for_ambiguous_submit(client_order_id)
        await self._audit({**audit, "status": "submit_unknown", "error": error, "recovery": recovery})
        await AlertService(self.db).emit(
            user_id,
            "live_order_submit_unknown",
            "critical",
            f"Live order {live_order_id} outcome is unknown; live orders are halted until it is resolved",
            {"live_order_id": live_order_id, "client_order_id": client_order_id, "error": error},
        )
        return {
            "success": False,
            "status": "submit_unknown",
            "live_order_id": live_order_id,
            "error": error,
            "halt": halt,
            "recovery": recovery,
            "audit": audit,
        }

    async def resolve_unknown(self, user_id: str, live_order_id: str) -> dict[str, Any]:
        """Settle a submit_unknown order by looking it up by client order id. Never re-submits."""
        latest = await self.lifecycle.states.latest_transition(live_order_id)
        if not latest or latest.get("user_id") != user_id:
            return {"success": False, "status": "not_found", "live_order_id": live_order_id}
        if latest.get("state") != LiveOrderState.SUBMIT_UNKNOWN.value:
            return {"success": False, "status": "not_unknown", "live_order_id": live_order_id, "state": latest.get("state")}

        client_order_id = latest.get("client_order_id")
        base_audit = {"user_id": user_id, "live_order_id": live_order_id, "client_order_id": client_order_id}
        try:
            exchange_order = await self.adapter.get_order_by_client_oid(client_order_id)
        except Exception as exc:
            # The outcome is still unknown: keep the state and the halt.
            await self._audit({**base_audit, "status": "resolve_lookup_failed", "error": str(exc)})
            return {"success": False, "status": "submit_unknown", "live_order_id": live_order_id, "resolution": "lookup_failed", "error": str(exc)}

        reconciliation_requirement = None
        if exchange_order is None:
            order = None
            status = LiveOrderState.FAILED.value
            await self.lifecycle.states.transition(
                order_id=live_order_id,
                next_state=status,
                reason="exchange has no order with this client order id; nothing was placed",
                metadata={"client_order_id": client_order_id},
            )
        else:
            order = self.adapter.normalize_order_response(
                exchange_order,
                symbol=latest["symbol"],
                side=latest["side"],
                requested={"client_order_id": client_order_id},
            )
            status = order["status"]
            if status == LiveOrderState.FAILED.value:
                await self.lifecycle.states.transition(order_id=live_order_id, next_state=status, reason="exchange reports order failed", metadata={"order": order})
            else:
                reconciliation_requirement = await self.lifecycle.finalized_from_order(user_id=user_id, live_order_id=live_order_id, order=order, dry_run=False)

        released = await self.lifecycle.pre_submit.release_halts(
            user_id=user_id,
            scope="user",
            released_by=user_id,
            reason=self.unknown_outcome_halt_reason(live_order_id),
        )
        await self._audit({**base_audit, "status": "submit_unknown_resolved", "resolved_status": status, "order": order})
        return {
            "success": True,
            "status": status,
            "live_order_id": live_order_id,
            "order": order,
            "released_halts": released,
            "reconciliation_requirement": reconciliation_requirement,
        }

    async def list_audits(self, user_id: str, limit: int = 100):
        return await self.db.live_order_audits.find(
            {"user_id": user_id},
            {"_id": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)

    async def verify_audit_chain(self, user_id: str):
        return await self.audits.verify_user_chain(user_id)
