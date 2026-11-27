from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app_state import db, logger
from auth_core import TokenResponse, UserCreate, UserLogin, get_current_user, login, signup
from services.advanced_risk_manager import AdvancedRiskManager
from services.market_data_service import MarketDataService
from services.paper_execution_adapter_v2 import PaperExecutionAdapterV2
from services.portfolio_service_v2 import PortfolioServiceV2

api_router = APIRouter(prefix="/api")


class BotConfigUpdate(BaseModel):
    """Fields a user may change through POST /bot-config; only the fields sent are stored.

    There is no is_active field: only /bot/start and /bot/stop start or stop a bot
    (an is_active key in the body is ignored like any other unknown key).
    """

    model_config = ConfigDict(extra="ignore")
    capital_floor: float | None = None  # deprecated: accepted so old clients do not get 422, never stored
    symbols: list[str] | None = None
    max_daily_loss: float | None = Field(default=None, gt=0, le=0.10)
    max_drawdown_pct: float | None = Field(default=None, gt=0, le=0.25)
    max_position_notional: float | None = Field(default=None, ge=5, le=10000)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    risk_target_vol: float | None = None

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, symbols: list[str] | None) -> list[str] | None:
        if symbols is None:
            return symbols
        if not symbols:
            raise ValueError("symbols must not be empty")
        if len(set(symbols)) != len(symbols):
            raise ValueError("symbols must not repeat")
        unsupported = sorted(set(symbols) - PaperExecutionAdapterV2.SUPPORTED_SYMBOLS)
        if unsupported:
            raise ValueError(f"unsupported symbols: {', '.join(unsupported)}")
        return symbols


def portfolio() -> PortfolioServiceV2:
    return PortfolioServiceV2(db)


@api_router.post("/auth/signup", response_model=TokenResponse)
async def signup_route(user_data: UserCreate):
    return await signup(user_data)


@api_router.post("/auth/login", response_model=TokenResponse)
async def login_route(credentials: UserLogin):
    return await login(credentials)


@api_router.get("/trades")
async def get_trades(current_user: dict = Depends(get_current_user)):
    return {"trades": await portfolio().get_trades(current_user["id"], limit=100)}


@api_router.get("/positions")
async def get_positions(current_user: dict = Depends(get_current_user)):
    return {"positions": await portfolio().mark_positions(current_user["id"])}


@api_router.get("/risk-metrics")
async def get_risk_metrics(current_user: dict = Depends(get_current_user)):
    metrics = await db.risk_metrics.find_one({"user_id": current_user["id"]}, {"_id": 0}, sort=[("timestamp", -1)])
    if not metrics:
        metrics = await portfolio().update_risk_snapshot(current_user["id"])
    metrics.pop("_id", None)
    return metrics


@api_router.get("/bot-config")
async def get_bot_config(current_user: dict = Depends(get_current_user)):
    return await db.bot_configs.find_one({"user_id": current_user["id"]}, {"_id": 0})


@api_router.post("/bot-config")
async def update_bot_config(config_update: BotConfigUpdate, current_user: dict = Depends(get_current_user)):
    changes = config_update.model_dump(exclude_unset=True, exclude_none=True, exclude={"capital_floor"})
    changes["updated_at"] = datetime.now(UTC).isoformat()
    await db.bot_configs.update_one(
        {"user_id": current_user["id"]},
        {"$set": changes, "$setOnInsert": {"is_active": False}},
        upsert=True,
    )
    return {"status": "success", "config": await db.bot_configs.find_one({"user_id": current_user["id"]}, {"_id": 0})}


@api_router.get("/market-analysis")
async def get_market_analysis(symbol: str = "BTC-USD", current_user: dict = Depends(get_current_user)):
    analysis = await db.market_analysis.find_one({"symbol": symbol}, {"_id": 0}, sort=[("timestamp", -1)])
    if not analysis:
        return {"message": "No analysis available yet"}
    return analysis


@api_router.get("/technical-indicators/{symbol}")
async def get_technical_indicators(symbol: str, current_user: dict = Depends(get_current_user)):
    analysis = await db.market_analysis.find_one({"symbol": symbol}, {"_id": 0}, sort=[("timestamp", -1)])
    if analysis and "technical_indicators" in analysis:
        return {
            "symbol": symbol,
            "technical_indicators": analysis["technical_indicators"],
            "technical_signals": analysis.get("technical_signals", {}),
            "timestamp": analysis.get("timestamp"),
        }
    return {"message": "No technical data available yet"}


@api_router.get("/market-data/{symbol}")
async def get_market_data(symbol: str, current_user: dict = Depends(get_current_user)):
    market = MarketDataService(db=db)
    try:
        return await market.get_current_price(symbol)
    except Exception as exc:
        logger.error("Error fetching market data: %s", exc)
        raise HTTPException(status_code=503, detail="Market data unavailable") from exc
    finally:
        await market.close()


@api_router.post("/bot/start")
async def start_bot(current_user: dict = Depends(get_current_user)):
    await portfolio().ensure_account_state(current_user["id"])
    await db.bot_configs.update_one(
        {"user_id": current_user["id"]},
        {"$set": {"is_active": True, "updated_at": datetime.now(UTC).isoformat()}},
    )
    logger.info("Bot activation requested for user %s", current_user["id"])
    return {"status": "Bot started", "is_active": True}


@api_router.post("/bot/stop")
async def stop_bot(current_user: dict = Depends(get_current_user)):
    await db.bot_configs.update_one(
        {"user_id": current_user["id"]},
        {"$set": {"is_active": False, "updated_at": datetime.now(UTC).isoformat()}},
    )
    logger.info("Bot stop requested for user %s", current_user["id"])
    return {"status": "Bot stopped", "is_active": False}


@api_router.get("/risk/advanced-assessment")
async def get_advanced_risk_assessment(current_user: dict = Depends(get_current_user)):
    metrics = await db.risk_metrics.find_one({"user_id": current_user["id"]}, {"_id": 0}, sort=[("timestamp", -1)])
    if not metrics:
        metrics = await portfolio().update_risk_snapshot(current_user["id"])
    positions = await portfolio().mark_positions(current_user["id"])
    recent_trades = await portfolio().get_trades(current_user["id"], limit=30)
    return AdvancedRiskManager().get_risk_assessment(metrics, positions, recent_trades)


@api_router.get("/performance/metrics")
async def get_performance_metrics(current_user: dict = Depends(get_current_user)):
    trades = await portfolio().get_trades(current_user["id"], limit=1000)
    total_trades = len(trades)
    winning = [t for t in trades if t.get("realized_pnl", t.get("pnl", 0)) > 0]
    losing = [t for t in trades if t.get("realized_pnl", t.get("pnl", 0)) < 0]
    win_rate = (len(winning) / total_trades * 100) if total_trades else 0
    avg_profit = sum(t.get("realized_pnl", t.get("pnl", 0)) for t in winning) / len(winning) if winning else 0
    avg_loss = sum(t.get("realized_pnl", t.get("pnl", 0)) for t in losing) / len(losing) if losing else 0
    total_profit = sum(t.get("realized_pnl", t.get("pnl", 0)) for t in winning)
    total_loss = sum(t.get("realized_pnl", t.get("pnl", 0)) for t in losing)
    metrics = await db.risk_metrics.find_one({"user_id": current_user["id"]}, {"_id": 0}, sort=[("timestamp", -1)])
    if not metrics:
        metrics = await portfolio().update_risk_snapshot(current_user["id"])
    roi = ((metrics.get("total_equity", 10000.0) - 10000.0) / 10000.0 * 100)
    return {
        "total_trades": total_trades,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(win_rate, 2),
        "avg_profit": round(avg_profit, 2),
        "avg_loss": round(avg_loss, 2),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
        "net_pnl": round(total_profit + total_loss, 2),
        "roi": round(roi, 2),
    }


@api_router.get("/dashboard/stats")
async def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    return await portfolio().get_dashboard_stats(current_user["id"])
