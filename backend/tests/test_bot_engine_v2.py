from datetime import UTC, datetime, timedelta

import pytest
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

from services.bot_engine import BotEngine
from services.execution_control_v2 import ExecutionControlV2
from services.market_data_service import MarketDataService, MarketDataUnavailable
from services.risk_guard_v2 import RiskGuardV2

NOON = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _matches(doc, query):
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
        elif isinstance(cond, dict):
            value = doc.get(key)
            for op, operand in cond.items():
                if op == "$exists" and (key in doc) != operand:
                    return False
                if op == "$lte" and not (value is not None and value <= operand):
                    return False
                if op == "$gte" and not (value is not None and value >= operand):
                    return False
                if op == "$in" and value not in operand:
                    return False
                if op == "$ne" and value == operand:
                    return False
        elif doc.get(key) != cond:
            return False
    return True


def _apply_update(doc, update):
    for key, value in update.get("$set", {}).items():
        doc[key] = value
    for key, value in update.get("$inc", {}).items():
        doc[key] = doc.get(key, 0) + value


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda doc: doc.get(key, ""), reverse=direction < 0)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.bulk_calls = []

    async def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if _matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda doc: doc.get(key, ""), reverse=direction < 0)
        return dict(matches[0]) if matches else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        doc = next((d for d in self.docs if _matches(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = {k: v for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
            self.docs.append(doc)
        _apply_update(doc, update)

    async def update_many(self, query, update):
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)

    async def find_one_and_update(self, query, update, upsert=False, return_document=None, projection=None):
        await self.update_one(query, update, upsert=upsert)
        return await self.find_one({k: v for k, v in query.items() if not k.startswith("$")})

    async def bulk_write(self, ops, ordered=True):
        self.bulk_calls.append((list(ops), ordered))
        for op in ops:
            assert isinstance(op, UpdateOne)
            await self.update_one(op._filter, op._doc, upsert=True)

    async def delete_one(self, query):
        self.docs = [doc for doc in self.docs if not _matches(doc, query)]

    def find(self, query, projection=None):
        return FakeCursor([dict(doc) for doc in self.docs if _matches(doc, query)])

    async def count_documents(self, query):
        return len([doc for doc in self.docs if _matches(doc, query)])


class FakeDB:
    """In-memory Mongo stand-in shared by bot-engine tests; collections appear on demand."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        collection = FakeCollection()
        setattr(self, name, collection)
        return collection


@pytest.mark.asyncio
async def test_bot_persists_candles_it_reads():
    db = FakeDB()
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))
    await engine.process_symbol("u1", "BTC-USD", [], {}, RiskGuardV2(), {"BTC-USD": 45000.0})
    assert len(db.market_candles.docs) == 120


@pytest.mark.asyncio
async def test_only_new_closed_candles_are_written_in_one_bulk_call():
    db = FakeDB()
    clock = {"now": NOON}
    svc = MarketDataService(db=db, source="simulated", clock=lambda: clock["now"])
    await svc.get_candles("BTC-USD", periods=120)
    clock["now"] = NOON + timedelta(minutes=1)
    await svc.get_candles("BTC-USD", periods=120)
    calls = db.market_candles.bulk_calls
    assert [len(ops) for ops, _ in calls] == [120, 1]
    assert all(ordered is False for _, ordered in calls)


@pytest.mark.asyncio
async def test_replay_reads_stored_candles():
    db = FakeDB()
    await MarketDataService(db=db, source="simulated", clock=lambda: NOON).get_candles("BTC-USD", periods=30)
    replay = MarketDataService(db=db, source="replay", replay_exchange="simulation")
    candles = await replay.get_candles("BTC-USD", periods=30)
    assert len(candles) == 30
    ticker = await replay.get_current_price("BTC-USD")
    assert ticker["price"] == candles[-1]["close"]
    assert ticker["bid"] is None and ticker["ask"] is None
    assert len(db.market_candles.bulk_calls) == 1  # replay does not write


@pytest.mark.asyncio
async def test_replay_reads_only_the_configured_exchange():
    db = FakeDB()
    sim = MarketDataService(db=db, source="simulated", clock=lambda: NOON)
    candles = await sim.get_candles("BTC-USD", periods=10)
    other = [{**c, "exchange": "coinbase_exchange", "close": 1.0, "price": 1.0} for c in candles]
    await sim.persist_candles(other)
    assert len(db.market_candles.docs) == 20

    replay = MarketDataService(db=db, source="replay", replay_exchange="simulation")
    replayed = await replay.get_candles("BTC-USD", periods=20)
    assert len(replayed) == 10
    assert all(c["exchange"] == "simulation" for c in replayed)
    assert (await replay.get_current_price("BTC-USD"))["price"] == candles[-1]["close"]

    default = MarketDataService(db=db, source="replay")
    assert default.replay_exchange == "coinbase_exchange"
    assert (await default.get_current_price("BTC-USD"))["price"] == 1.0


@pytest.mark.asyncio
async def test_replay_with_nothing_stored_is_unavailable():
    replay = MarketDataService(db=FakeDB(), source="replay")
    with pytest.raises(MarketDataUnavailable):
        await replay.get_candles("BTC-USD", periods=30)
    with pytest.raises(MarketDataUnavailable):
        await replay.get_current_price("BTC-USD")


@pytest.mark.asyncio
async def test_failing_symbol_is_skipped_and_others_still_trade(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True, "symbols": ["BTC-USD", "ETH-USD"]})

    class FlakyMarket(MarketDataService):
        async def get_historical_data(self, symbol, periods=100, timeframe="1m"):
            if symbol == "ETH-USD":
                raise MarketDataUnavailable("ETH feed down")
            return await super().get_historical_data(symbol, periods, timeframe)

    engine = BotEngine(db, market=FlakyMarket(db=db, source="simulated", clock=lambda: NOON))
    processed_symbols = []

    def build_plan(symbol, prices, has_position, base_notional=100.0):
        processed_symbols.append(symbol)
        return {"symbol": symbol, "action": "HOLD"}

    monkeypatch.setattr(engine.planner, "build_plan", build_plan)
    await engine.cycle("u1")
    assert any(a["type"] == "market_data_failed" for a in db.alerts.docs)
    assert processed_symbols == ["BTC-USD"]


@pytest.mark.asyncio
async def test_cycle_rolls_daily_start_on_the_market_clock(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True, "symbols": ["BTC-USD"]})
    db.portfolio_state.docs.append({
        "user_id": "u1",
        "cash_balance": 10000.0,
        "realized_pnl": 0.0,
        "equity_high": 10000.0,
        "daily_start_equity": 10000.0,
        "daily_start_date": "2026-11-25",
    })
    just_after_midnight = datetime(2026, 11, 26, 0, 0, 5, tzinfo=UTC)
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: just_after_midnight))
    monkeypatch.setattr(engine.planner, "build_plan", lambda symbol, prices, has_position, base_notional=100.0: {"symbol": symbol, "action": "HOLD"})
    await engine.cycle("u1")
    state = await db.portfolio_state.find_one({"user_id": "u1"})
    assert state["daily_start_date"] == "2026-11-26"


@pytest.mark.asyncio
async def test_execute_buy_snapshots_on_the_market_clock(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    scripted_day = datetime(2026, 1, 15, 0, 0, 5, tzinfo=UTC)
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: scripted_day))
    context = {
        "action": "BUY",
        "selected": {"strategy": "trend"},
        "notional": 50.0,
        "timeframe": "1m",
        "decision_candle_close": scripted_day.isoformat(),
    }
    await engine.execute_buy("u1", "BTC-USD", 50.0, context)
    assert db.positions_v2.docs
    state = await db.portfolio_state.find_one({"user_id": "u1"})
    assert state["daily_start_date"] == "2026-01-15"


@pytest.mark.asyncio
async def test_paper_fills_use_the_engine_market():
    market = MarketDataService(db=FakeDB(), source="simulated", clock=lambda: NOON)
    engine = BotEngine(FakeDB(), market=market)
    assert engine.execution.trading_service.adapter.market_data is market


def build(side, snap):
    return ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", side, snap)


def test_idempotency_key_differs_per_decision_candle():
    snap = {"selected": {"strategy": "trend"}, "timeframe": "1m", "decision_candle_close": "2026-11-26T12:00:00+00:00"}
    later = {**snap, "decision_candle_close": "2026-11-26T12:01:00+00:00"}
    assert build("BUY", snap) != build("BUY", later)
    assert build("BUY", snap) == build("BUY", dict(snap))


def test_idempotency_key_requires_decision_time():
    with pytest.raises(ValueError):
        ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", "BUY", {"selected": {"strategy": "t"}})


def test_idempotency_key_includes_timeframe():
    snap = {"selected": {"strategy": "trend"}, "timeframe": "1m", "decision_candle_close": "2026-11-26T12:00:00+00:00"}
    assert build("BUY", snap) != build("BUY", {**snap, "timeframe": "1h"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("filled", True), ("partially_filled", True), ("unknown", True), ("rejected", False)])
async def test_any_non_rejected_attempt_counts_as_executed(status, expected):
    db = FakeDB()
    await db.trades_v2.insert_one({"idempotency_key": "k", "status": status})
    assert await ExecutionControlV2(db).already_executed("k") is expected


@pytest.mark.asyncio
async def test_bot_can_buy_again_after_round_trip(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True, "symbols": ["BTC-USD"]})
    clock = {"now": NOON}
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: clock["now"]))

    # The 300 s trade cooldown compares against wall-clock last_trade_at; let the guard see the same advance as the market clock.
    started = datetime.now(UTC)
    monkeypatch.setattr(RiskGuardV2, "utc_now", staticmethod(lambda: started + (clock["now"] - NOON)))

    scripted = iter(["BUY", "SELL", "BUY"])

    def build_plan(symbol, prices, has_position, base_notional=100.0):
        return {"symbol": symbol, "action": next(scripted), "selected": {"strategy": "trend"}, "notional": 50.0}

    monkeypatch.setattr(engine.planner, "build_plan", build_plan)
    for step in range(3):
        clock["now"] = NOON + timedelta(minutes=6 * step)
        await engine.cycle("u1")

    assert [t["side"] for t in db.trades_v2.docs if t["status"] == "filled"] == ["BUY", "SELL", "BUY"]
    assert len({t["idempotency_key"] for t in db.trades_v2.docs}) == 3


@pytest.mark.asyncio
async def test_duplicate_trade_insert_records_no_fill(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))

    async def reject_duplicate(doc):
        raise DuplicateKeyError("uniq_trade_idempotency_claim")

    monkeypatch.setattr(db.trades_v2, "insert_one", reject_duplicate)
    context = {
        "action": "BUY",
        "selected": {"strategy": "trend"},
        "notional": 50.0,
        "timeframe": "1m",
        "decision_candle_close": NOON.isoformat(),
    }
    await engine.execute_buy("u1", "BTC-USD", 50.0, context)

    assert db.positions_v2.docs == []
    assert db.ledger_entries.docs == []
