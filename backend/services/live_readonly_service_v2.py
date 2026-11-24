import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from services.alert_service import AlertService
from services.coinbase_readonly_adapter_v2 import CoinbaseReadonlyAdapterV2, CoinbaseReadonlyError
from services.ledger_service_v2 import LedgerServiceV2
from services.portfolio_service_v2 import PortfolioServiceV2


class LiveReadonlyServiceV2:
    """Live-readonly orchestration service with persisted freshness metadata."""

    def __init__(self, db, adapter: CoinbaseReadonlyAdapterV2 | None = None):
        self.db = db
        self.adapter = adapter or CoinbaseReadonlyAdapterV2()
        self.alerts = AlertService(db)
        self.portfolio = PortfolioServiceV2(db)
        self.ledger = LedgerServiceV2(db)

    @staticmethod
    def utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def parse_timestamp(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def max_snapshot_age_seconds() -> int:
        try:
            return int(os.getenv("LIVE_READONLY_MAX_SNAPSHOT_AGE_SECONDS", "300"))
        except ValueError:
            return 300

    @staticmethod
    def canonical_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

    @staticmethod
    def _symbol_from_currency(currency: str) -> str | None:
        if currency in {"BTC", "ETH"}:
            return f"{currency}-USD"
        return None

    @staticmethod
    def _base_currency(symbol: str) -> str:
        return symbol.split("-")[0]

    async def persist_snapshot(self, user_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        record = {
            "user_id": user_id,
            "mode": "live-readonly",
            "exchange": snapshot.get("exchange", "coinbase_exchange"),
            "adapter_version": snapshot.get("adapter_version", "unknown"),
            "credential_alias": snapshot.get("credential_alias", "unknown"),
            "snapshot": snapshot,
            "snapshot_hash": self.canonical_hash(snapshot),
            "created_at": self.utc_now(),
            "snapshot_timestamp": snapshot.get("timestamp"),
            "orders_allowed": False,
            "live_execution_enabled": False,
        }
        await self.db.live_readonly_snapshots.insert_one(record)
        return record

    async def latest_snapshot_status(self, user_id: str) -> dict[str, Any]:
        latest = await self.db.live_readonly_snapshots.find_one({"user_id": user_id}, {"_id": 0}, sort=[("created_at", -1)])
        if not latest:
            return {"status": "missing", "fresh": False, "reason": "no live-readonly snapshot found", "max_age_seconds": self.max_snapshot_age_seconds()}
        timestamp = self.parse_timestamp(latest.get("snapshot_timestamp") or latest.get("created_at"))
        if not timestamp:
            return {"status": "invalid", "fresh": False, "reason": "latest live-readonly snapshot timestamp is invalid", "snapshot": latest}
        age = (datetime.now(UTC) - timestamp).total_seconds()
        max_age = self.max_snapshot_age_seconds()
        return {
            "status": "fresh" if age <= max_age else "stale",
            "fresh": age <= max_age,
            "age_seconds": age,
            "max_age_seconds": max_age,
            "snapshot_hash": latest.get("snapshot_hash"),
            "snapshot_timestamp": latest.get("snapshot_timestamp"),
            "created_at": latest.get("created_at"),
            "exchange": latest.get("exchange"),
            "adapter_version": latest.get("adapter_version"),
            "credential_alias": latest.get("credential_alias"),
        }

    async def snapshot(self, user_id: str, symbols: list[str] | None = None) -> dict[str, Any]:
        try:
            raw = await self.adapter.snapshot(symbols=symbols)
        except CoinbaseReadonlyError as exc:
            await self.alerts.emit(user_id, "live_readonly_snapshot_failed", "error", str(exc), {"error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}})
            raise

        raw["user_id"] = user_id
        raw["internal_positions"] = await self.portfolio.get_positions(user_id)
        raw["ledger_rebuild"] = await self.ledger.rebuild_from_ledger(user_id)
        persisted = await self.persist_snapshot(user_id, raw)
        raw["snapshot_hash"] = persisted["snapshot_hash"]
        raw["persisted_at"] = persisted["created_at"]
        return raw

    async def compare_exchange_to_internal(self, user_id: str, symbols: list[str] | None = None, tolerance_units: float = 1e-8) -> dict[str, Any]:
        snapshot = await self.snapshot(user_id, symbols=symbols)
        exchange_accounts = snapshot.get("accounts", [])
        internal_positions = snapshot.get("internal_positions", [])

        exchange_units_by_symbol: dict[str, float] = {}
        for account in exchange_accounts:
            symbol = self._symbol_from_currency(str(account.get("currency", "")))
            if symbol:
                exchange_units_by_symbol[symbol] = exchange_units_by_symbol.get(symbol, 0.0) + float(account.get("balance", 0.0) or 0.0)

        internal_units_by_symbol = {position["symbol"]: float(position.get("base_units", 0.0) or 0.0) for position in internal_positions}

        issues = []
        for symbol in sorted(set(exchange_units_by_symbol) | set(internal_units_by_symbol)):
            exchange_units = round(exchange_units_by_symbol.get(symbol, 0.0), 12)
            internal_units = round(internal_units_by_symbol.get(symbol, 0.0), 12)
            delta = round(exchange_units - internal_units, 12)
            if abs(delta) > tolerance_units:
                issues.append({"type": "exchange_internal_position_drift", "symbol": symbol, "exchange_units": exchange_units, "internal_units": internal_units, "delta_units": delta})

        report = {
            "user_id": user_id,
            "status": "ok" if not issues else "mismatch",
            "mode": "live-readonly",
            "orders_allowed": False,
            "live_execution_enabled": False,
            "issues": issues,
            "snapshot_hash": snapshot.get("snapshot_hash"),
            "checked_at": self.utc_now(),
            "snapshot": snapshot,
        }
        await self.db.live_readonly_reports.insert_one(report.copy())
        if issues:
            await self.alerts.emit(user_id, "live_readonly_drift_detected", "warning", "Exchange readonly balances differ from internal positions", {"issues": issues, "snapshot_hash": snapshot.get("snapshot_hash")})
        return report

    async def recent_orders(self, user_id: str, status: str = "all", limit: int = 100) -> dict[str, Any]:
        try:
            orders = await self.adapter.get_orders(status=status, limit=limit)
        except CoinbaseReadonlyError as exc:
            await self.alerts.emit(user_id, "live_readonly_orders_failed", "error", str(exc), {"error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}})
            raise
        return {"user_id": user_id, "mode": "live-readonly", "orders_allowed": False, "orders": orders, "checked_at": self.utc_now()}

    async def recent_fills(self, user_id: str, product_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        try:
            fills = await self.adapter.get_fills(product_id=product_id, limit=limit)
        except CoinbaseReadonlyError as exc:
            await self.alerts.emit(user_id, "live_readonly_fills_failed", "error", str(exc), {"error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}})
            raise
        return {"user_id": user_id, "mode": "live-readonly", "orders_allowed": False, "fills": fills, "checked_at": self.utc_now()}
