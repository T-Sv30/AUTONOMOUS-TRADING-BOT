from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool

from api_routes_v2 import api_router
from app_state import db
from auth_core import get_current_user
from runtime_config import DEBUG, OPS_ADMIN_EMAILS, OPS_ADMIN_ENABLED
from services.alert_service import AlertService
from services.backtesting_service_v2 import BacktestConfig, BacktestingServiceV2
from services.coinbase_live_execution_adapter_v2 import CoinbaseLiveExecutionError
from services.coinbase_readonly_adapter_v2 import CoinbaseReadonlyError
from services.ledger_service_v2 import LedgerServiceV2
from services.live_pre_submit_safety_service_v2 import LivePreSubmitSafetyServiceV2
from services.live_readonly_service_v2 import LiveReadonlyServiceV2
from services.live_trading_gate_v2 import LiveTradingGateV2
from services.live_trading_service_v2 import LiveTradingServiceV2
from services.manual_live_pilot_readiness_service_v2 import ManualLivePilotReadinessServiceV2
from services.manual_live_pilot_review_service_v2 import ManualLivePilotReviewServiceV2
from services.manual_live_pilot_workflow_service_v2 import ManualLivePilotWorkflowServiceV2
from services.market_data_service import MarketDataService, MarketDataUnavailable
from services.mongo_indexes_v2 import MongoIndexServiceV2
from services.operational_readiness_v2 import OperationalReadinessServiceV2
from services.trading_mode_v2 import TradingModeService
from services.worker_heartbeat_service_v2 import WorkerHeartbeatServiceV2


class BacktestRequest(BaseModel):
    symbol: str = "BTC-USD"
    timeframe: str = "1h"
    periods: int = Field(default=300, ge=30, le=300)
    initial_cash: float = Field(default=10000.0, gt=0)
    fee_bps: float = Field(default=10.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    max_position_pct: float = Field(default=0.30, gt=0, le=1.0)
    max_drawdown_pct: float = Field(default=0.10, ge=0, le=1.0)
    max_daily_loss_pct: float = Field(default=0.05, ge=0, le=1.0)
    fast_window: int = Field(default=10, ge=2, le=100)
    slow_window: int = Field(default=30, ge=3, le=200)
    min_trade_notional: float = Field(default=10.0, ge=0)

    def to_config(self) -> BacktestConfig:
        return BacktestConfig(
            initial_cash=self.initial_cash,
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
            max_position_pct=self.max_position_pct,
            max_drawdown_pct=self.max_drawdown_pct,
            max_daily_loss_pct=self.max_daily_loss_pct,
            fast_window=self.fast_window,
            slow_window=self.slow_window,
            min_trade_notional=self.min_trade_notional,
            timeframe=self.timeframe,
        )


class WalkForwardRequest(BacktestRequest):
    train_periods: int = Field(default=120, ge=30, le=300)
    test_periods: int = Field(default=60, ge=30, le=300)
    periods: int = Field(default=300, ge=90, le=300)


class ReconciliationRequest(BaseModel):
    starting_cash: float = Field(default=10000.0, gt=0)


class LiveReadonlySymbolsRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTC-USD", "ETH-USD"])


class LiveMarketBuyRequest(BaseModel):
    symbol: str = "BTC-USD"
    notional_usd: float = Field(gt=0)
    approval_token: str | None = None
    # Strict: "false", 0, "no" or null must not coerce into a non-dry-run live order.
    dry_run: StrictBool = True


class LiveMarketSellRequest(BaseModel):
    symbol: str = "BTC-USD"
    base_units: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    approval_token: str | None = None
    # Strict: "false", 0, "no" or null must not coerce into a non-dry-run live order.
    dry_run: StrictBool = True


class PilotReconciliationResolutionRequest(BaseModel):
    live_order_id: str
    resolution: str = Field(default="verified_after_live_readonly_reconciliation")
    notes: str | None = None


class PilotReportRequest(BaseModel):
    live_order_id: str


class PilotSignoffRequest(BaseModel):
    live_order_id: str
    decision: str = Field(default="approved_for_next_tiny_pilot")
    notes: str | None = None


class EmergencyHaltRequest(BaseModel):
    reason: str = Field(default="operator emergency halt")
    # "global" halts live orders for every user and needs an ops admin.
    scope: Literal["user", "global"] = "user"


class EmergencyHaltReleaseRequest(BaseModel):
    scope: Literal["user", "global"] = "user"
    # For a user halt: whose halt to release. Defaults to the caller.
    user_id: str | None = None


def is_ops_admin(user: dict) -> bool:
    if DEBUG and not OPS_ADMIN_ENABLED:
        return True
    email = str(user.get("email", "")).lower().strip()
    return bool(OPS_ADMIN_ENABLED and email in OPS_ADMIN_EMAILS)


async def require_ops_admin(current_user: dict = Depends(get_current_user)):
    if is_ops_admin(current_user):
        return current_user
    raise HTTPException(status_code=403, detail="Admin privileges required")


@api_router.get("/trading-mode")
async def get_trading_mode(current_user: dict = Depends(get_current_user)):
    return TradingModeService().describe()


@api_router.get("/alerts")
async def get_alerts(current_user: dict = Depends(get_current_user), limit: int = 100):
    return {"alerts": await AlertService(db).list_alerts(current_user["id"], limit=limit)}


@api_router.post("/backtests/run")
async def run_backtest(request: BacktestRequest, current_user: dict = Depends(get_current_user)):
    market = MarketDataService(db=db)
    try:
        candles = await market.get_candles(request.symbol, timeframe=request.timeframe, periods=request.periods)
    except (ValueError, MarketDataUnavailable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await market.close()
    result = BacktestingServiceV2().run_moving_average_backtest(candles, request.to_config())
    result["user_id"] = current_user["id"]
    result["symbol"] = request.symbol
    result["timeframe"] = request.timeframe
    return result


@api_router.post("/backtests/walk-forward")
async def run_walk_forward(request: WalkForwardRequest, current_user: dict = Depends(get_current_user)):
    market = MarketDataService(db=db)
    try:
        candles = await market.get_candles(request.symbol, timeframe=request.timeframe, periods=request.periods)
    except (ValueError, MarketDataUnavailable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await market.close()
    result = BacktestingServiceV2().walk_forward_validation(candles, train_periods=request.train_periods, test_periods=request.test_periods, config=request.to_config())
    result["user_id"] = current_user["id"]
    result["symbol"] = request.symbol
    result["timeframe"] = request.timeframe
    return result


@api_router.get("/ledger/entries")
async def get_ledger_entries(current_user: dict = Depends(get_current_user), limit: int = 250):
    return {"entries": await LedgerServiceV2(db).list_entries(current_user["id"], limit=limit)}


@api_router.post("/ledger/rebuild")
async def rebuild_ledger_state(request: ReconciliationRequest, current_user: dict = Depends(get_current_user)):
    return await LedgerServiceV2(db).rebuild_from_ledger(current_user["id"], starting_cash=request.starting_cash)


@api_router.post("/ledger/reconcile")
async def reconcile_ledger_state(request: ReconciliationRequest, current_user: dict = Depends(get_current_user)):
    return await LedgerServiceV2(db).reconcile(current_user["id"], starting_cash=request.starting_cash)


@api_router.post("/live-readonly/snapshot")
async def get_live_readonly_snapshot(request: LiveReadonlySymbolsRequest, current_user: dict = Depends(get_current_user)):
    try:
        return await LiveReadonlyServiceV2(db).snapshot(current_user["id"], symbols=request.symbols)
    except CoinbaseReadonlyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api_router.get("/live-readonly/status")
async def get_live_readonly_status(current_user: dict = Depends(get_current_user)):
    return await LiveReadonlyServiceV2(db).latest_snapshot_status(current_user["id"])


@api_router.post("/live-readonly/reconcile")
async def reconcile_live_readonly(request: LiveReadonlySymbolsRequest, current_user: dict = Depends(get_current_user)):
    try:
        return await LiveReadonlyServiceV2(db).compare_exchange_to_internal(current_user["id"], symbols=request.symbols)
    except CoinbaseReadonlyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api_router.get("/live-readonly/orders")
async def get_live_readonly_orders(current_user: dict = Depends(get_current_user), status: str = "all", limit: int = 100):
    try:
        return await LiveReadonlyServiceV2(db).recent_orders(current_user["id"], status=status, limit=limit)
    except CoinbaseReadonlyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api_router.get("/live-readonly/fills")
async def get_live_readonly_fills(current_user: dict = Depends(get_current_user), product_id: str | None = None, limit: int = 100):
    try:
        return await LiveReadonlyServiceV2(db).recent_fills(current_user["id"], product_id=product_id, limit=limit)
    except CoinbaseReadonlyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api_router.get("/live-trading/gate")
async def get_live_trading_gate(current_user: dict = Depends(get_current_user)):
    return LiveTradingGateV2().describe()


@api_router.get("/live-trading/pilot-readiness")
async def get_manual_live_pilot_readiness(current_user: dict = Depends(get_current_user)):
    return await ManualLivePilotReadinessServiceV2(db).checklist(current_user["id"])


@api_router.get("/live-trading/pilot/pending-reconciliation")
async def get_manual_live_pilot_pending_reconciliation(current_user: dict = Depends(get_current_user), limit: int = 100):
    return await ManualLivePilotWorkflowServiceV2(db).pending_reconciliation_requirements(current_user["id"], limit=limit)


@api_router.post("/live-trading/pilot/resolve-reconciliation")
async def resolve_manual_live_pilot_reconciliation(request: PilotReconciliationResolutionRequest, current_user: dict = Depends(get_current_user)):
    return await ManualLivePilotWorkflowServiceV2(db).resolve_reconciliation_requirement(user_id=current_user["id"], live_order_id=request.live_order_id, resolution=request.resolution, notes=request.notes)


@api_router.post("/live-trading/pilot/report")
async def build_manual_live_pilot_report(request: PilotReportRequest, current_user: dict = Depends(get_current_user)):
    return await ManualLivePilotWorkflowServiceV2(db).build_pilot_report(current_user["id"], request.live_order_id)


@api_router.get("/live-trading/pilot/reports")
async def list_manual_live_pilot_reports(current_user: dict = Depends(get_current_user), limit: int = 100):
    return await ManualLivePilotWorkflowServiceV2(db).list_pilot_reports(current_user["id"], limit=limit)


@api_router.get("/live-trading/pilot/expansion-status")
async def get_manual_live_pilot_expansion_status(current_user: dict = Depends(get_current_user)):
    return await ManualLivePilotReviewServiceV2(db).expansion_status(current_user["id"])


@api_router.post("/live-trading/pilot/signoff")
async def signoff_manual_live_pilot_report(request: PilotSignoffRequest, current_user: dict = Depends(get_current_user)):
    return await ManualLivePilotReviewServiceV2(db).signoff_report(user_id=current_user["id"], live_order_id=request.live_order_id, operator_id=current_user["id"], decision=request.decision, notes=request.notes)


@api_router.get("/live-trading/pilot/signoffs")
async def list_manual_live_pilot_signoffs(current_user: dict = Depends(get_current_user), limit: int = 100):
    return await ManualLivePilotReviewServiceV2(db).list_signoffs(current_user["id"], limit=limit)


@api_router.post("/live-trading/pilot/unresolved-reconciliation-alerts")
async def emit_manual_live_unresolved_reconciliation_alerts(current_user: dict = Depends(get_current_user)):
    return await ManualLivePilotReviewServiceV2(db).emit_unresolved_reconciliation_alerts(current_user["id"])


@api_router.post("/live-trading/market-buy")
async def live_market_buy(request: LiveMarketBuyRequest, current_user: dict = Depends(get_current_user)):
    try:
        return await LiveTradingServiceV2(db).place_market_buy(current_user["id"], request.symbol, request.notional_usd, approval_token=request.approval_token, dry_run=request.dry_run)
    except CoinbaseLiveExecutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api_router.post("/live-trading/market-sell")
async def live_market_sell(request: LiveMarketSellRequest, current_user: dict = Depends(get_current_user)):
    try:
        return await LiveTradingServiceV2(db).place_market_sell(current_user["id"], request.symbol, request.base_units, reference_price=request.reference_price, approval_token=request.approval_token, dry_run=request.dry_run)
    except CoinbaseLiveExecutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api_router.post("/live-trading/orders/{live_order_id}/resolve")
async def resolve_unknown_live_order(live_order_id: str, current_user: dict = Depends(get_current_user)):
    result = await LiveTradingServiceV2(db).resolve_unknown(current_user["id"], live_order_id)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="live order not found")
    if result["status"] == "not_unknown":
        raise HTTPException(status_code=409, detail=f"live order is {result.get('state')}, not submit_unknown")
    return result


@api_router.get("/live-trading/audits")
async def get_live_order_audits(current_user: dict = Depends(get_current_user), limit: int = 100):
    return {"audits": await LiveTradingServiceV2(db).list_audits(current_user["id"], limit=limit)}


@api_router.get("/ops/readiness")
async def get_operational_readiness(current_user: dict = Depends(get_current_user), strict: bool = False):
    return await OperationalReadinessServiceV2().readiness(db, strict=strict)


@api_router.get("/ops/workers")
async def get_worker_status(current_user: dict = Depends(get_current_user), limit: int = 100):
    return await WorkerHeartbeatServiceV2(db).list_workers(limit=limit)


@api_router.post("/ops/workers/stale-alerts")
async def emit_worker_stale_alerts(current_user: dict = Depends(require_ops_admin), limit: int = 100):
    return await WorkerHeartbeatServiceV2(db).emit_stale_worker_alerts(limit=limit)


@api_router.post("/ops/indexes/ensure")
async def ensure_mongo_indexes(current_user: dict = Depends(require_ops_admin)):
    return await MongoIndexServiceV2(db).ensure_indexes()


@api_router.post("/ops/emergency-halt")
async def emergency_halt(request: EmergencyHaltRequest, current_user: dict = Depends(get_current_user)):
    if request.scope == "global" and not is_ops_admin(current_user):
        raise HTTPException(status_code=403, detail="Admin privileges required for a global halt")
    now = datetime.now(UTC).isoformat()
    result = await db.bot_configs.update_many({"user_id": current_user["id"]}, {"$set": {"is_active": False, "halt_reason": request.reason, "updated_at": now}})
    # The live_halts record survives restarts and blocks every later live order until an ops admin releases it.
    halt = await LivePreSubmitSafetyServiceV2(db).create_halt(
        user_id=None if request.scope == "global" else current_user["id"],
        scope=request.scope,
        reason=request.reason,
        created_by=current_user["id"],
    )
    await AlertService(db).emit(current_user["id"], "operator_emergency_halt", "critical", f"Emergency halt triggered: {request.reason}", {"modified_count": getattr(result, "modified_count", None), "reason": request.reason, "scope": request.scope})
    return {"status": "halted", "reason": request.reason, "scope": request.scope, "halt": halt, "modified_count": getattr(result, "modified_count", None), "timestamp": now}


@api_router.post("/ops/emergency-halt/release")
async def release_emergency_halt(request: EmergencyHaltReleaseRequest, current_user: dict = Depends(require_ops_admin)):
    now = datetime.now(UTC).isoformat()
    target_user_id = None if request.scope == "global" else (request.user_id or current_user["id"])
    released = await LivePreSubmitSafetyServiceV2(db).release_halts(user_id=target_user_id, scope=request.scope, released_by=current_user["id"])
    await AlertService(db).emit(current_user["id"], "operator_emergency_halt_released", "warning", f"Emergency halt released ({request.scope})", {"scope": request.scope, "user_id": target_user_id, "released_count": released})
    return {"status": "released", "scope": request.scope, "user_id": target_user_id, "released_count": released, "timestamp": now}
