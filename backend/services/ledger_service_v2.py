import hashlib
from datetime import UTC, datetime
from typing import Any


class LedgerServiceV2:
    """Append-only ledger and reconciliation helper.

    The ledger is the audit source for cash, fees, fills, and realized PnL.
    Existing portfolio state/positions remain fast read models that can be
    reconciled against ledger-derived balances.
    """

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def stable_entry_id(entry: dict[str, Any]) -> str:
        material = "|".join(
            str(entry.get(key, ""))
            for key in [
                "user_id",
                "event_type",
                "symbol",
                "side",
                "client_order_id",
                "order_id",
                "amount_usd",
                "base_units",
                "sequence",
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def append_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        now = self.utc_now()
        normalized = {
            "user_id": entry["user_id"],
            "event_type": entry["event_type"],
            "symbol": entry.get("symbol"),
            "side": entry.get("side"),
            "amount_usd": round(float(entry.get("amount_usd", 0.0) or 0.0), 8),
            "base_units": round(float(entry.get("base_units", 0.0) or 0.0), 12),
            "price": round(float(entry.get("price", 0.0) or 0.0), 8),
            "fee_usd": round(float(entry.get("fee_usd", 0.0) or 0.0), 8),
            "realized_pnl": round(float(entry.get("realized_pnl", 0.0) or 0.0), 8),
            "cash_delta": round(float(entry.get("cash_delta", 0.0) or 0.0), 8),
            "base_delta": round(float(entry.get("base_delta", 0.0) or 0.0), 12),
            "cost_basis_delta": round(float(entry.get("cost_basis_delta", 0.0) or 0.0), 8),
            "order_id": entry.get("order_id"),
            "client_order_id": entry.get("client_order_id"),
            "idempotency_key": entry.get("idempotency_key"),
            "source": entry.get("source", "portfolio_service_v2"),
            "metadata": entry.get("metadata", {}),
            "sequence": int(entry.get("sequence", 0)),
            "created_at": entry.get("created_at") or now,
        }
        normalized["entry_id"] = entry.get("entry_id") or self.stable_entry_id(normalized)
        await self.db.ledger_entries.insert_one(normalized)
        return normalized

    async def record_buy_fill(
        self,
        user_id: str,
        symbol: str,
        filled_price: float,
        base_units: float,
        notional_usd: float,
        fee_usd: float = 0.0,
        order: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        order = order or {}
        gross_notional = float(notional_usd)
        fee = float(fee_usd or 0.0)
        cost_basis = gross_notional
        common = {
            "user_id": user_id,
            "symbol": symbol,
            "side": "BUY",
            "price": filled_price,
            "order_id": order.get("order_id"),
            "client_order_id": order.get("client_order_id"),
            "idempotency_key": order.get("idempotency_key"),
            "metadata": {"order": order},
        }
        entries = [
            await self.append_entry(
                {
                    **common,
                    "event_type": "BUY_FILL",
                    "amount_usd": gross_notional,
                    "base_units": base_units,
                    "cash_delta": -cost_basis,
                    "base_delta": base_units,
                    "cost_basis_delta": cost_basis,
                    "fee_usd": fee,
                    "sequence": 1,
                }
            ),
            await self.append_entry(
                {
                    **common,
                    "event_type": "FEE",
                    "amount_usd": fee,
                    "cash_delta": 0.0,
                    "fee_usd": fee,
                    "sequence": 2,
                }
            ),
        ]
        return entries

    async def record_sell_fill(
        self,
        user_id: str,
        symbol: str,
        filled_price: float,
        base_units: float,
        gross_proceeds: float,
        net_proceeds: float,
        fee_usd: float = 0.0,
        sold_cost_basis: float = 0.0,
        realized_pnl: float = 0.0,
        order: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        order = order or {}
        common = {
            "user_id": user_id,
            "symbol": symbol,
            "side": "SELL",
            "price": filled_price,
            "order_id": order.get("order_id"),
            "client_order_id": order.get("client_order_id"),
            "idempotency_key": order.get("idempotency_key"),
            "metadata": {"order": order},
        }
        return [
            await self.append_entry(
                {
                    **common,
                    "event_type": "SELL_FILL",
                    "amount_usd": gross_proceeds,
                    "base_units": base_units,
                    "cash_delta": net_proceeds,
                    "base_delta": -float(base_units),
                    "cost_basis_delta": -float(sold_cost_basis),
                    "fee_usd": fee_usd,
                    "realized_pnl": realized_pnl,
                    "sequence": 1,
                }
            ),
            await self.append_entry(
                {
                    **common,
                    "event_type": "REALIZED_PNL",
                    "amount_usd": realized_pnl,
                    "realized_pnl": realized_pnl,
                    "sequence": 2,
                }
            ),
            await self.append_entry(
                {
                    **common,
                    "event_type": "FEE",
                    "amount_usd": fee_usd,
                    "fee_usd": fee_usd,
                    "sequence": 3,
                }
            ),
        ]

    async def list_entries(self, user_id: str, limit: int = 250) -> list[dict[str, Any]]:
        return await self.db.ledger_entries.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)

    async def rebuild_from_ledger(self, user_id: str, starting_cash: float = 10000.0) -> dict[str, Any]:
        entries = await self.db.ledger_entries.find({"user_id": user_id}, {"_id": 0}).sort("created_at", 1).to_list(10000)
        cash_balance = float(starting_cash)
        realized_pnl = 0.0
        positions: dict[str, dict[str, float]] = {}

        for entry in entries:
            symbol = entry.get("symbol")
            event_type = entry.get("event_type")
            cash_balance += float(entry.get("cash_delta", 0.0) or 0.0)
            # SELL_FILL entries carry realized_pnl for display; the REALIZED_PNL entry is the
            # single accounting source, so counting both would double realized PnL.
            if event_type == "REALIZED_PNL":
                realized_pnl += float(entry.get("realized_pnl", 0.0) or 0.0)
            if symbol:
                position = positions.setdefault(symbol, {"base_units": 0.0, "notional_usd": 0.0, "fees_paid_usd": 0.0})
                position["base_units"] += float(entry.get("base_delta", 0.0) or 0.0)
                position["notional_usd"] += float(entry.get("cost_basis_delta", 0.0) or 0.0)
                if event_type == "FEE":
                    position["fees_paid_usd"] += float(entry.get("fee_usd", 0.0) or 0.0)

        normalized_positions = []
        for symbol, position in positions.items():
            units = round(position["base_units"], 12)
            notional = max(0.0, round(position["notional_usd"], 8))
            if abs(units) <= 1e-12:
                continue
            normalized_positions.append(
                {
                    "symbol": symbol,
                    "base_units": units,
                    "notional_usd": notional,
                    "avg_price": round(notional / units, 8) if units else 0.0,
                    "fees_paid_usd": round(position["fees_paid_usd"], 8),
                }
            )

        return {
            "user_id": user_id,
            "cash_balance": round(cash_balance, 8),
            "realized_pnl": round(realized_pnl, 8),
            "positions": normalized_positions,
            "ledger_entries": len(entries),
            "rebuilt_at": self.utc_now(),
        }

    async def reconcile(self, user_id: str, starting_cash: float = 10000.0, tolerance: float = 1e-6) -> dict[str, Any]:
        rebuilt = await self.rebuild_from_ledger(user_id, starting_cash=starting_cash)
        state = await self.db.portfolio_state.find_one({"user_id": user_id}, {"_id": 0}) or {}
        current_positions = await self.db.positions_v2.find({"user_id": user_id}, {"_id": 0}).to_list(1000)

        issues = []
        current_cash = round(float(state.get("cash_balance", starting_cash)), 8)
        if abs(current_cash - rebuilt["cash_balance"]) > tolerance:
            issues.append(
                {
                    "type": "cash_mismatch",
                    "current": current_cash,
                    "rebuilt": rebuilt["cash_balance"],
                    "delta": round(current_cash - rebuilt["cash_balance"], 8),
                }
            )

        current_by_symbol = {p["symbol"]: p for p in current_positions}
        rebuilt_by_symbol = {p["symbol"]: p for p in rebuilt["positions"]}
        for symbol in sorted(set(current_by_symbol) | set(rebuilt_by_symbol)):
            current = current_by_symbol.get(symbol, {})
            rebuilt_position = rebuilt_by_symbol.get(symbol, {})
            for key in ["base_units", "notional_usd"]:
                current_value = round(float(current.get(key, 0.0) or 0.0), 12 if key == "base_units" else 8)
                rebuilt_value = round(float(rebuilt_position.get(key, 0.0) or 0.0), 12 if key == "base_units" else 8)
                if abs(current_value - rebuilt_value) > tolerance:
                    issues.append(
                        {
                            "type": "position_mismatch",
                            "symbol": symbol,
                            "field": key,
                            "current": current_value,
                            "rebuilt": rebuilt_value,
                            "delta": round(current_value - rebuilt_value, 12 if key == "base_units" else 8),
                        }
                    )

        report = {
            "user_id": user_id,
            "status": "ok" if not issues else "mismatch",
            "issues": issues,
            "rebuilt": rebuilt,
            "checked_at": self.utc_now(),
        }
        await self.db.reconciliation_reports.insert_one(report.copy())
        return report
