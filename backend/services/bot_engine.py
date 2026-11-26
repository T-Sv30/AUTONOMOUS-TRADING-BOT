import asyncio
import logging
from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from services.alert_service import AlertService
from services.execution_control_v2 import ExecutionControlV2
from services.execution_service_v2 import ExecutionServiceV2
from services.market_data_service import MarketDataService, MarketDataUnavailable
from services.paper_execution_adapter_v2 import PaperExecutionAdapterV2
from services.portfolio_service_v2 import PortfolioServiceV2
from services.risk_guard_v2 import RiskGuardV2
from services.signal_planner_v2 import SignalPlannerV2
from services.structured_logging import log_event
from services.trading_mode_v2 import TradingModeError, TradingModeService
from services.trading_service_v2 import TradingServiceV2

logger = logging.getLogger(__name__)


class BotEngine:
    def __init__(self, db, market: MarketDataService | None = None):
        self.db = db
        self.alerts = AlertService(db)
        self.market = market or MarketDataService(db=db)
        # Decisions and paper fills read prices through the same market service.
        self.execution = ExecutionServiceV2(TradingServiceV2(PaperExecutionAdapterV2(market_data=self.market)))
        self.execution_control = ExecutionControlV2(db)
        self.portfolio = PortfolioServiceV2(db)
        self.planner = SignalPlannerV2()
        self.mode = TradingModeService()
        self.running = False

    async def start(self, user_id: str):
        self.running = True
        log_event(logger, logging.INFO, "bot_started", user_id=user_id)

        while self.running:
            try:
                await self.cycle(user_id)
                await asyncio.sleep(8)
            except Exception as e:
                log_event(logger, logging.ERROR, "bot_cycle_failed", user_id=user_id, error=str(e))
                await self.alerts.emit(user_id, "bot_cycle_failed", "error", "Bot cycle failed", {"error": str(e)})
                await asyncio.sleep(3)

    async def stop(self):
        self.running = False
        await self.market.close()

    def _now(self) -> datetime:
        clock = getattr(self.market, "clock", None)
        return clock() if clock else datetime.now(UTC)

    async def cycle(self, user_id: str):
        config = await self.db.bot_configs.find_one({"user_id": user_id})
        if not config or not config.get("is_active"):
            return

        risk = RiskGuardV2.from_config(config)
        await self.portfolio.ensure_account_state(user_id)
        symbols = config.get("symbols", ["BTC-USD"])
        price_map = await self.get_price_map(user_id, symbols)
        snapshot = await self.portfolio.update_risk_snapshot(user_id, price_map, now=self._now(), max_drawdown_pct=risk.max_drawdown_pct)
        state = await self.db.portfolio_state.find_one({"user_id": user_id}) or {}
        positions = await self.portfolio.get_positions(user_id)
        metrics = risk.portfolio_metrics(state, positions, price_map)
        metrics["daily_loss_pct"] = max(metrics.get("daily_loss_pct", 0.0), max(0.0, -float(snapshot.get("daily_pnl", 0.0)) / (float(snapshot.get("max_equity", 1.0)) or 1.0)))
        metrics["drawdown_pct"] = max(metrics.get("drawdown_pct", 0.0), float(snapshot.get("current_drawdown", 0.0)))
        kill = risk.should_kill_switch(metrics)

        if metrics.get("drawdown_pct", 0.0) >= (risk.max_drawdown_pct * 0.8):
            await self.alerts.emit(user_id, "abnormal_drawdown", "warning", "Drawdown is approaching halt threshold", metrics)

        if kill.get("triggered"):
            await self.halt_bot(user_id, kill.get("reason"), metrics)
            return

        if self.mode.is_readonly:
            log_event(logger, logging.INFO, "execution_disabled_readonly", user_id=user_id)
            return

        for symbol in symbols:
            positions = await self.portfolio.get_positions(user_id)
            state = await self.db.portfolio_state.find_one({"user_id": user_id}) or {}
            try:
                await self.process_symbol(user_id, symbol, positions, state, risk, price_map)
            except MarketDataUnavailable as exc:
                await self.report_market_data_failure(user_id, symbol, exc)

    async def halt_bot(self, user_id: str, reason: str, metrics=None):
        await self.db.bot_configs.update_one(
            {"user_id": user_id},
            {"$set": {"is_active": False, "halt_reason": reason, "updated_at": datetime.now(UTC).isoformat()}},
        )
        log_event(logger, logging.WARNING, "bot_halted", user_id=user_id, reason=reason)
        await self.alerts.emit(user_id, "bot_halted", "critical", f"Bot halted: {reason}", metrics or {})

    async def get_price_map(self, user_id, symbols):
        price_map = {}
        for symbol in symbols:
            try:
                current = await self.market.get_current_price(symbol)
                price_map[symbol] = float(current.get("price", 0.0))
            except Exception as exc:
                await self.report_market_data_failure(user_id, symbol, exc)
        return price_map

    async def report_market_data_failure(self, user_id, symbol, exc):
        log_event(logger, logging.WARNING, "market_data_failed", user_id=user_id, symbol=symbol, error=str(exc))
        await self.alerts.emit(user_id, "market_data_failed", "warning", f"Market data unavailable for {symbol}", {"symbol": symbol, "error": str(exc)})

    async def process_symbol(self, user_id, symbol, positions, state, risk: RiskGuardV2, price_map):
        timeframe = "1m"
        hist = await self.market.get_historical_data(symbol, periods=120, timeframe=timeframe)
        prices = [h["price"] for h in hist if h.get("price")]
        position = next((p for p in positions if p["symbol"] == symbol), None)
        plan = self.planner.build_plan(symbol, prices, has_position=bool(position))
        # The idempotency key is per decision: the last closed candle the plan was built from.
        plan["timeframe"] = timeframe
        plan["decision_candle_close"] = hist[-1]["timestamp"] if hist else None

        if plan["action"] == "BUY" and not position:
            metrics = risk.portfolio_metrics(state, positions, {**price_map, symbol: prices[-1] if prices else 0.0})
            guard = risk.can_open_position(metrics, positions, plan.get("notional", 0.0), state.get("last_trade_at"), now=self._now())
            if guard["allowed"]:
                await self.execute_buy(user_id, symbol, guard["notional"], plan)
            else:
                log_event(logger, logging.INFO, "buy_blocked", user_id=user_id, symbol=symbol, reason=guard.get("reason"))

        elif plan["action"] == "SELL" and position:
            await self.execute_sell(user_id, symbol, position, plan)

    async def execute_buy(self, user_id, symbol, notional, context):
        try:
            self.mode.assert_can_trade()
        except TradingModeError as exc:
            log_event(logger, logging.WARNING, "execution_mode_blocked", user_id=user_id, symbol=symbol, side="BUY", error=str(exc))
            await self.alerts.emit(user_id, "execution_mode_blocked", "warning", str(exc), {"symbol": symbol, "side": "BUY"})
            return

        idempotency_key = self.execution_control.build_idempotency_key(user_id, symbol, "BUY", context)
        if await self.execution_control.already_executed(idempotency_key):
            log_event(logger, logging.INFO, "duplicate_order_skipped", user_id=user_id, symbol=symbol, side="BUY")
            return

        lock = await self.execution_control.acquire_lock(user_id, symbol, "BUY")
        if not lock.get("acquired"):
            log_event(logger, logging.INFO, "execution_lock_blocked", user_id=user_id, symbol=symbol, side="BUY", reason=lock.get("reason"))
            return

        try:
            order = await self.execution.buy(symbol, notional, signal_snapshot=context, idempotency_key=idempotency_key)
            order["idempotency_key"] = idempotency_key
            try:
                await self.portfolio.record_trade_attempt(user_id, symbol, "BUY", order, context)
            except DuplicateKeyError:
                # Backstop behind the lock and already_executed: another attempt already claimed this key.
                log_event(logger, logging.WARNING, "duplicate_order_skipped", user_id=user_id, symbol=symbol, side="BUY", idempotency_key=idempotency_key)
                return
            if not order.get("success"):
                log_event(logger, logging.ERROR, "execution_failed", user_id=user_id, symbol=symbol, side="BUY", order=order)
                await self.alerts.emit(user_id, "execution_failed", "error", f"Buy failed for {symbol}", order)
                return

            await self.portfolio.record_buy_fill(user_id, symbol, order["filled_price"], order["base_units"], order["notional_usd"], order.get("fee_usd", 0.0), order=order)
            await self.db.portfolio_state.update_one({"user_id": user_id}, {"$set": {"last_trade_at": self._now().isoformat()}}, upsert=True)
            await self.portfolio.update_risk_snapshot(user_id, {symbol: float(order["filled_price"])}, now=self._now())
        finally:
            await self.execution_control.release_lock(lock["key"])

    async def execute_sell(self, user_id, symbol, position, context):
        try:
            self.mode.assert_can_trade()
        except TradingModeError as exc:
            log_event(logger, logging.WARNING, "execution_mode_blocked", user_id=user_id, symbol=symbol, side="SELL", error=str(exc))
            await self.alerts.emit(user_id, "execution_mode_blocked", "warning", str(exc), {"symbol": symbol, "side": "SELL"})
            return

        idempotency_key = self.execution_control.build_idempotency_key(user_id, symbol, "SELL", context)
        if await self.execution_control.already_executed(idempotency_key):
            log_event(logger, logging.INFO, "duplicate_order_skipped", user_id=user_id, symbol=symbol, side="SELL")
            return

        lock = await self.execution_control.acquire_lock(user_id, symbol, "SELL")
        if not lock.get("acquired"):
            log_event(logger, logging.INFO, "execution_lock_blocked", user_id=user_id, symbol=symbol, side="SELL", reason=lock.get("reason"))
            return

        try:
            order = await self.execution.sell(symbol, position["base_units"], idempotency_key=idempotency_key)
            order["idempotency_key"] = idempotency_key
            try:
                await self.portfolio.record_trade_attempt(user_id, symbol, "SELL", order, context)
            except DuplicateKeyError:
                # Backstop behind the lock and already_executed: another attempt already claimed this key.
                log_event(logger, logging.WARNING, "duplicate_order_skipped", user_id=user_id, symbol=symbol, side="SELL", idempotency_key=idempotency_key)
                return
            if not order.get("success"):
                log_event(logger, logging.ERROR, "execution_failed", user_id=user_id, symbol=symbol, side="SELL", order=order)
                await self.alerts.emit(user_id, "execution_failed", "error", f"Sell failed for {symbol}", order)
                return

            result = await self.portfolio.record_sell_fill(user_id, symbol, order["filled_price"], order["base_units"], order.get("fee_usd", 0.0), order=order)
            await self.db.trades_v2.update_one({"client_order_id": order.get("client_order_id")}, {"$set": {"realized_pnl": result.get("realized_pnl", 0.0), "idempotency_key": idempotency_key}})
            await self.db.portfolio_state.update_one({"user_id": user_id}, {"$set": {"last_trade_at": self._now().isoformat()}}, upsert=True)
            await self.portfolio.update_risk_snapshot(user_id, {symbol: float(order["filled_price"])}, now=self._now())
        finally:
            await self.execution_control.release_lock(lock["key"])
