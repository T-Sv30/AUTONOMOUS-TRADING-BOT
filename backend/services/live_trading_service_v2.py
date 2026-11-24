from datetime import UTC, datetime
from typing import Any

from services.coinbase_live_execution_adapter_v2 import (
    CoinbaseLiveExecutionAdapterV2,
    CoinbaseLiveExecutionError,
)
from services.execution_service_v2 import ExecutionServiceV2
from services.live_manual_order_lifecycle_service_v2 import (
    LiveManualOrderLifecycleServiceV2,
)
from services.live_order_audit_service_v2 import LiveOrderAuditServiceV2
from services.live_trading_gate_v2 import LiveTradingGateV2
from services.trading_mode_v2 import TradingModeService


class LiveTradingServiceV2:
    """Gated manual live execution orchestration.

    This service is the manual live order entrypoint. It keeps the existing live
    gate and adapter boundaries, while wiring in:

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
                "user_id": user_id,
                "symbol": symbol,
                "side": side,
                "notional_usd": round(float(notional_usd), 8),
                "base_units": round(float(base_units), 12) if base_units is not None else None,
                "reference_price": reference_price,
                "client_order_id": client_order_id,
                "live_order_id": live_order_id,
                "dry_run": dry_run,
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
        except CoinbaseLiveExecutionError as exc:
            await self.lifecycle.adapter_error(live_order_id, str(exc))
            await self._audit(
                {
                    **audit,
                    "status": "adapter_error",
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

    async def list_audits(self, user_id: str, limit: int = 100):
        return await self.db.live_order_audits.find(
            {"user_id": user_id},
            {"_id": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)

    async def verify_audit_chain(self, user_id: str, limit: int = 1000):
        return await self.audits.verify_user_chain(user_id, limit=limit)
