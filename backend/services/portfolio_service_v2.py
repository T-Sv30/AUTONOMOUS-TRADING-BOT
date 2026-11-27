from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from services.ledger_service_v2 import LedgerServiceV2

FILLED_STATUSES = ("filled", "partially_filled")


class PortfolioServiceV2:
    def __init__(self, db):
        self.db = db
        self.ledger = LedgerServiceV2(db)

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    async def ensure_account_state(self, user_id: str, starting_cash: float = 10000.0, now: datetime | None = None) -> dict[str, Any]:
        """Return the account state, opening it on ``now`` (the caller's clock; wall time by default) if missing."""
        state = await self.db.portfolio_state.find_one({"user_id": user_id}, {"_id": 0})
        if state:
            return state

        opened_at = (now or datetime.now(UTC)).astimezone(UTC)
        state = {
            "user_id": user_id,
            "starting_cash": float(starting_cash),
            "cash_balance": float(starting_cash),
            "realized_pnl": 0.0,
            "equity_high": float(starting_cash),
            "daily_start_equity": float(starting_cash),
            "daily_start_date": opened_at.date().isoformat(),
            "updated_at": opened_at.isoformat(),
        }
        await self.db.portfolio_state.insert_one(dict(state))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
        return state

    async def get_positions(self, user_id: str) -> list[dict[str, Any]]:
        return await self.db.positions_v2.find({"user_id": user_id}, {"_id": 0}).to_list(100)

    async def mark_positions(self, user_id: str, price_map: dict[str, float] | None = None) -> list[dict[str, Any]]:
        positions = await self.get_positions(user_id)
        price_map = price_map or {}
        marked = []

        for position in positions:
            quantity = float(position.get("base_units", 0.0))
            avg_price = float(position.get("avg_price", 0.0))
            current_price = float(price_map.get(position.get("symbol"), avg_price))
            market_value = quantity * current_price
            cost_basis = float(position.get("notional_usd", 0.0))
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
            marked.append({
                "user_id": user_id,
                "symbol": position.get("symbol"),
                "quantity": round(quantity, 12),
                "base_units": round(quantity, 12),
                "avg_price": round(avg_price, 8),
                "current_price": round(current_price, 8),
                "notional_usd": round(cost_basis, 8),
                "market_value": round(market_value, 8),
                "pnl": round(pnl, 8),
                "pnl_percent": round(pnl_percent, 8),
                "fees_paid_usd": round(float(position.get("fees_paid_usd", 0.0)), 8),
                "created_at": position.get("created_at"),
                "updated_at": position.get("updated_at"),
            })
        return marked

    async def claim_trade(
        self,
        user_id: str,
        symbol: str,
        side: str,
        idempotency_key: str,
        client_order_id: str | None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Claim one decision before its order is sent. False means another attempt already holds the claim.

        The pending document holds the key in the uniq_trade_idempotency_claim partial index until
        record_trade_attempt updates it: a fill keeps the claim, a rejection releases it.
        """
        claim = {
            "user_id": user_id,
            "symbol": symbol,
            "side": side,
            "order_type": "market",
            "status": "pending",
            "client_order_id": client_order_id,
            "idempotency_key": idempotency_key,
            "idempotency_claim": True,
            "signal_snapshot": context or {},
            "created_at": self.utc_now(),
            "filled_at": None,
        }
        try:
            await self.db.trades_v2.insert_one(claim)
        except DuplicateKeyError:
            return False
        return True

    async def record_trade_attempt(
        self,
        user_id: str,
        symbol: str,
        side: str,
        order: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record the adapter's result on the pending claim for its key, or insert it when nothing was claimed."""
        now = self.utc_now()
        trade = {
            "user_id": user_id,
            "symbol": symbol,
            "side": side,
            "order_type": "market",
            "status": order.get("status", "unknown"),
            "order_id": order.get("order_id"),
            "client_order_id": order.get("client_order_id"),
            "idempotency_key": order.get("idempotency_key"),
            "requested_notional_usd": order.get("requested_notional_usd"),
            "requested_base_units": order.get("requested_base_units"),
            "notional_usd": order.get("notional_usd"),
            "base_units": order.get("base_units"),
            "filled_price": order.get("filled_price"),
            "fee_usd": round(float(order.get("fee_usd", 0.0) or 0.0), 8),
            "simulation": bool(order.get("simulation", False)),
            "paper_execution": bool(order.get("paper_execution", False)),
            "execution_adapter": order.get("execution_adapter"),
            "signal_snapshot": order.get("signal_snapshot") or context or {},
            "created_at": now,
            "filled_at": now if order.get("status") in {"filled", "partially_filled"} else None,
        }
        if trade["idempotency_key"]:
            # Holds the key in the uniq_trade_idempotency_claim partial index (partial indexes cannot filter on $ne).
            # A rejected attempt releases it, so a retry on the same candle can claim again.
            trade["idempotency_claim"] = trade["status"] != "rejected"
            claimed = {"user_id": user_id, "idempotency_key": trade["idempotency_key"], "status": "pending", "idempotency_claim": True}
            update = {k: v for k, v in trade.items() if k != "created_at"}
            result = await self.db.trades_v2.update_one(claimed, {"$set": update})
            if result.matched_count:
                return
        await self.db.trades_v2.insert_one(trade)

    async def get_trades(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return await self.db.trades_v2.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)

    async def update_risk_snapshot(self, user_id: str, price_map: dict[str, float] | None = None, now: datetime | None = None, max_drawdown_pct: float = 0.05) -> dict[str, Any]:
        state = await self.ensure_account_state(user_id, now=now)
        positions = await self.mark_positions(user_id, price_map)

        cash_balance = float(state.get("cash_balance", 0.0))
        positions_value = sum(float(position.get("market_value", 0.0)) for position in positions)
        total_equity = cash_balance + positions_value
        max_equity = max(float(state.get("equity_high", total_equity)), total_equity)
        today = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        if state.get("daily_start_date") != today:
            daily_start_equity = total_equity  # first snapshot of a new UTC day (or a legacy state without a date)
        else:
            daily_start_equity = float(state.get("daily_start_equity", max_equity))
        current_drawdown = (max_equity - total_equity) / max_equity if max_equity > 0 else 0.0
        daily_pnl = total_equity - daily_start_equity
        now = self.utc_now()

        snapshot = {
            "user_id": user_id,
            "total_equity": round(total_equity, 8),
            "max_equity": round(max_equity, 8),
            "equity_floor": round(max_equity * (1 - max_drawdown_pct), 8),
            "current_drawdown": round(current_drawdown, 8),
            "daily_pnl": round(daily_pnl, 8),
            "positions_value": round(positions_value, 8),
            "cash_balance": round(cash_balance, 8),
            "timestamp": now,
        }

        # created_at_ts is a BSON date for the risk_metrics_ttl index; the returned snapshot is unchanged.
        await self.db.risk_metrics.insert_one({**snapshot, "created_at_ts": datetime.now(UTC)})
        await self.db.portfolio_state.update_one(
            {"user_id": user_id},
            {"$set": {"equity_high": max_equity, "daily_start_equity": daily_start_equity, "daily_start_date": today, "updated_at": now}},
            upsert=True,
        )
        return snapshot

    async def get_dashboard_stats(self, user_id: str) -> dict[str, Any]:
        state = await self.ensure_account_state(user_id)
        metrics = await self.db.risk_metrics.find_one({"user_id": user_id}, {"_id": 0}, sort=[("timestamp", -1)])
        if not metrics:
            metrics = await self.update_risk_snapshot(user_id)

        positions = await self.get_positions(user_id)
        trades_count = await self.db.trades_v2.count_documents({"user_id": user_id, "status": {"$in": list(FILLED_STATUSES)}})
        config = await self.db.bot_configs.find_one({"user_id": user_id}, {"_id": 0})
        return {
            "total_equity": metrics.get("total_equity", 10000.0),
            "daily_pnl": metrics.get("daily_pnl", 0.0),
            "total_positions": len(positions),
            "total_trades": trades_count,
            "bot_active": config.get("is_active", False) if config else False,
            "current_drawdown": metrics.get("current_drawdown", 0.0),
            "cash_balance": metrics.get("cash_balance", state.get("cash_balance", 10000.0)),
            "positions_value": metrics.get("positions_value", 0.0),
        }

    async def record_buy_fill(
        self,
        user_id: str,
        symbol: str,
        filled_price: float,
        base_units: float,
        notional_usd: float,
        fee_usd: float = 0.0,
        order: dict[str, Any] | None = None,
    ) -> None:
        await self.ensure_account_state(user_id)
        fee_usd = float(fee_usd or 0.0)
        gross_notional = float(notional_usd)
        cost_basis = gross_notional
        units = float(base_units)
        existing = await self.db.positions_v2.find_one({"user_id": user_id, "symbol": symbol}, {"_id": 0})

        if existing:
            old_units = float(existing.get("base_units", 0.0))
            old_notional = float(existing.get("notional_usd", 0.0))
            old_fees = float(existing.get("fees_paid_usd", 0.0))
            new_units = old_units + units
            new_notional = old_notional + cost_basis
            new_avg = new_notional / new_units if new_units > 0 else float(filled_price)
            await self.db.positions_v2.update_one({"user_id": user_id, "symbol": symbol}, {"$set": {"base_units": round(new_units, 12), "notional_usd": round(new_notional, 8), "avg_price": round(new_avg, 8), "fees_paid_usd": round(old_fees + fee_usd, 8), "updated_at": self.utc_now()}})
        else:
            await self.db.positions_v2.insert_one({"user_id": user_id, "symbol": symbol, "base_units": round(units, 12), "notional_usd": round(cost_basis, 8), "avg_price": round(cost_basis / units, 8) if units > 0 else round(float(filled_price), 8), "fees_paid_usd": round(fee_usd, 8), "created_at": self.utc_now(), "updated_at": self.utc_now()})

        await self.db.portfolio_state.update_one({"user_id": user_id}, {"$inc": {"cash_balance": -cost_basis}, "$set": {"updated_at": self.utc_now()}}, upsert=True)
        await self.ledger.record_buy_fill(user_id, symbol, filled_price, units, gross_notional, fee_usd, order=order)

    async def record_sell_fill(
        self,
        user_id: str,
        symbol: str,
        filled_price: float,
        base_units: float,
        fee_usd: float = 0.0,
        order: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        position = await self.db.positions_v2.find_one({"user_id": user_id, "symbol": symbol})
        if not position:
            return {"realized_pnl": 0.0}
        current_units = float(position.get("base_units", 0.0))
        current_notional = float(position.get("notional_usd", 0.0))
        if current_units <= 0:
            return {"realized_pnl": 0.0}

        fee_usd = float(fee_usd or 0.0)
        sell_units = min(float(base_units), current_units)
        cost_basis_per_unit = current_notional / current_units
        sold_cost_basis = sell_units * cost_basis_per_unit
        gross_proceeds = sell_units * float(filled_price)
        net_proceeds = gross_proceeds - fee_usd
        realized_pnl = net_proceeds - sold_cost_basis
        remaining_units = current_units - sell_units
        remaining_notional = max(0.0, current_notional - sold_cost_basis)

        if remaining_units <= 1e-12:
            await self.db.positions_v2.delete_one({"user_id": user_id, "symbol": symbol})
        else:
            await self.db.positions_v2.update_one({"user_id": user_id, "symbol": symbol}, {"$set": {"base_units": round(remaining_units, 12), "notional_usd": round(remaining_notional, 8), "avg_price": round(remaining_notional / remaining_units, 8), "updated_at": self.utc_now()}})

        await self.db.portfolio_state.update_one({"user_id": user_id}, {"$inc": {"cash_balance": net_proceeds, "realized_pnl": realized_pnl}, "$set": {"updated_at": self.utc_now()}}, upsert=True)
        await self.ledger.record_sell_fill(user_id, symbol, filled_price, sell_units, gross_proceeds, net_proceeds, fee_usd, sold_cost_basis, realized_pnl, order=order)
        return {"realized_pnl": round(realized_pnl, 8), "gross_proceeds": round(gross_proceeds, 8), "net_proceeds": round(net_proceeds, 8), "fee_usd": round(fee_usd, 8), "remaining_units": round(max(remaining_units, 0.0), 12)}

    async def rebuild_from_ledger(self, user_id: str, starting_cash: float = 10000.0) -> dict[str, Any]:
        return await self.ledger.rebuild_from_ledger(user_id, starting_cash=starting_cash)

    async def reconcile_with_ledger(self, user_id: str, starting_cash: float = 10000.0) -> dict[str, Any]:
        return await self.ledger.reconcile(user_id, starting_cash=starting_cash)
