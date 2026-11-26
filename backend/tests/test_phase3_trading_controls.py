import pytest
from pymongo.errors import DuplicateKeyError

from api_routes_v2 import BotConfig
from services.execution_control_v2 import ExecutionControlV2
from services.execution_service_v2 import ExecutionServiceV2
from services.risk_guard_v2 import RiskGuardV2
from services.trading_mode_v2 import TradingModeError, TradingModeService


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.raise_duplicate_on_find_one_and_update = False

    def _matches(self, doc, query):
        for key, value in query.items():
            if key == "$or":
                if not any(self._matches(doc, item) for item in value):
                    return False
            elif isinstance(value, dict):
                if "$lte" in value and not doc.get(key, "") <= value["$lte"]:
                    return False
                if "$exists" in value and (key in doc) != value["$exists"]:
                    return False
                if "$ne" in value and doc.get(key) == value["$ne"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True

    async def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        return dict(matches[0]) if matches else None

    async def find_one_and_update(self, query, update, upsert=False, return_document=None, projection=None):
        if self.raise_duplicate_on_find_one_and_update:
            raise DuplicateKeyError("duplicate execution lock")
        doc = None
        for existing in self.docs:
            if self._matches(existing, query):
                doc = existing
                break
        if doc is None:
            if not upsert:
                return None
            key = query.get("key")
            if key and any(existing.get("key") == key for existing in self.docs):
                return None
            doc = {"key": key} if key else {}
            self.docs.append(doc)
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, value in update.get("$setOnInsert", {}).items():
            doc.setdefault(key, value)
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value
        if projection and projection.get("_id") == 0:
            return {key: value for key, value in doc.items() if key != "_id"}
        return dict(doc)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        doc = None
        for existing in self.docs:
            if self._matches(existing, query):
                doc = existing
                break
        if doc is None:
            if not upsert:
                return
            doc = dict(query)
            self.docs.append(doc)
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value

    async def delete_one(self, query):
        self.docs = [doc for doc in self.docs if not self._matches(doc, query)]

    def find(self, query, projection=None):
        matches = [dict(doc) for doc in self.docs if self._matches(doc, query)]
        return FakeCursor(matches)


class FakeDB:
    def __init__(self):
        self.execution_locks = FakeCollection()
        self.trades_v2 = FakeCollection()


def test_oversized_buy_is_clipped_not_rejected():
    guard = RiskGuardV2(max_position_notional=100)
    metrics = guard.portfolio_metrics({"cash_balance": 10000.0}, [], {})
    result = guard.can_open_position(metrics, [], proposed_notional=140.0)
    assert result == {"allowed": True, "reason": "ok", "notional": 100.0}


def test_drawdown_limit_is_the_only_equity_halt():
    guard = RiskGuardV2(max_drawdown_pct=0.05)
    m = guard.portfolio_metrics({"cash_balance": 9650.0, "equity_high": 10000.0, "daily_start_equity": 9650.0}, [], {})
    assert guard.should_kill_switch(m)["triggered"] is False  # 3.5% down: the old floor halted here
    m = guard.portfolio_metrics({"cash_balance": 9500.0, "equity_high": 10000.0, "daily_start_equity": 9500.0}, [], {})
    assert "drawdown" in guard.should_kill_switch(m)["reason"]


def test_legacy_capital_floor_is_accepted_and_ignored():
    guard = RiskGuardV2.from_config({"capital_floor": 0.99, "max_drawdown_pct": 0.05})
    m = guard.portfolio_metrics({"cash_balance": 9800.0, "equity_high": 10000.0, "daily_start_equity": 9800.0}, [], {})
    assert guard.should_kill_switch(m)["triggered"] is False  # 2% down
    assert BotConfig(capital_floor=0.97).capital_floor == 0.97


def test_trading_modes_are_explicit_and_gate_execution():
    paper = TradingModeService("paper")
    paper.assert_can_trade()
    assert paper.describe()["mode"] == "paper"
    assert paper.describe()["orders_allowed"] is True

    readonly = TradingModeService("live-readonly")
    with pytest.raises(TradingModeError):
        readonly.assert_can_trade()
    assert readonly.describe()["orders_allowed"] is False

    live = TradingModeService("live-trading")
    with pytest.raises(TradingModeError):
        live.assert_can_trade()

    with pytest.raises(TradingModeError):
        TradingModeService("bad-mode")


@pytest.mark.asyncio
async def test_execution_control_locks_and_idempotency():
    db = FakeDB()
    control = ExecutionControlV2(db, lock_ttl_seconds=60)

    first = await control.acquire_lock("user-1", "BTC-USD", "BUY")
    assert first["acquired"] is True

    second = await control.acquire_lock("user-1", "BTC-USD", "BUY")
    assert second["acquired"] is False

    await control.release_lock(first["key"])
    third = await control.acquire_lock("user-1", "BTC-USD", "BUY")
    assert third["acquired"] is True

    snapshot = {"selected": {"strategy": "test"}, "timeframe": "1m", "decision_candle_close": "2026-11-26T12:00:00+00:00"}
    key = control.build_idempotency_key("user-1", "BTC-USD", "BUY", snapshot)
    assert key == "user-1:BTC-USD:BUY:test:1m:2026-11-26T12:00:00+00:00"
    assert await control.already_executed(key) is False
    await db.trades_v2.insert_one({"idempotency_key": key, "status": "filled"})
    assert await control.already_executed(key) is True


@pytest.mark.asyncio
async def test_execution_control_denies_duplicate_key_race():
    db = FakeDB()
    db.execution_locks.raise_duplicate_on_find_one_and_update = True
    control = ExecutionControlV2(db, lock_ttl_seconds=60)

    result = await control.acquire_lock("user-1", "BTC-USD", "BUY")

    assert result["acquired"] is False
    assert result["reason"] == "execution lock active"


@pytest.mark.asyncio
async def test_execution_service_uses_stable_client_order_id_for_idempotency_key():
    service = ExecutionServiceV2()
    first = await service.buy("BTC-USD", 10.0, idempotency_key="same-key")
    second = await service.buy("BTC-USD", 10.0, idempotency_key="same-key")
    assert first["client_order_id"] == second["client_order_id"]
    assert first["idempotency_key"] == "same-key"
