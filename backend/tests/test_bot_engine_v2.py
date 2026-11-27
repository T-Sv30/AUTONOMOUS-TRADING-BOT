from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeDB
from pymongo.errors import DuplicateKeyError

from services.bot_engine import BotEngine
from services.execution_control_v2 import ExecutionControlV2
from services.market_data_service import MarketDataService, MarketDataUnavailable
from services.risk_guard_v2 import RiskGuardV2

NOON = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


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

    # The engine passes its market clock to the cooldown check, so this utc_now patch is a fallback only; kept from before that fix.
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


@pytest.mark.asyncio
async def test_oversized_buy_plan_is_clipped_to_the_cap_and_fills(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True, "symbols": ["BTC-USD"]})
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))
    monkeypatch.setattr(
        engine.planner,
        "build_plan",
        lambda symbol, prices, has_position, base_notional=100.0: {"symbol": symbol, "action": "BUY", "selected": {"strategy": "trend"}, "notional": 140.0},
    )
    received = []
    real_execute_buy = engine.execute_buy

    async def spy(user_id, symbol, notional, context):
        received.append(notional)
        return await real_execute_buy(user_id, symbol, notional, context)

    monkeypatch.setattr(engine, "execute_buy", spy)
    await engine.cycle("u1")

    assert received == [100.0]
    filled = [t for t in db.trades_v2.docs if t["status"] == "filled"]
    assert len(filled) == 1
    assert filled[0]["notional_usd"] == pytest.approx(100.0, rel=0.01)
    assert filled[0]["signal_snapshot"]["notional"] == 100.0


@pytest.mark.asyncio
async def test_trade_cooldown_runs_on_the_market_clock(monkeypatch):
    """last_trade_at and the cooldown check both read the engine's market clock, not wall time."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True, "symbols": ["BTC-USD"], "cooldown_seconds": 300})
    far_from_now = datetime(2031, 3, 4, 9, 0, tzinfo=UTC)
    clock = {"now": far_from_now}
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: clock["now"]))

    scripted = iter(["BUY", "SELL", "BUY", "BUY"])

    def build_plan(symbol, prices, has_position, base_notional=100.0):
        return {"symbol": symbol, "action": next(scripted), "selected": {"strategy": "trend"}, "notional": 50.0}

    monkeypatch.setattr(engine.planner, "build_plan", build_plan)

    async def filled_sides_after(seconds):
        clock["now"] = far_from_now + timedelta(seconds=seconds)
        await engine.cycle("u1")
        return [t["side"] for t in db.trades_v2.docs if t["status"] == "filled"]

    assert await filled_sides_after(0) == ["BUY"]
    assert await filled_sides_after(60) == ["BUY", "SELL"]
    # 60 scripted seconds after the sell: the 300 s cooldown blocks the buy.
    assert await filled_sides_after(120) == ["BUY", "SELL"]
    # 301 scripted seconds after the sell: the cooldown has passed on the market clock.
    assert await filled_sides_after(361) == ["BUY", "SELL", "BUY"]


@pytest.mark.asyncio
async def test_realized_pnl_lands_on_the_filled_sell_not_the_rejected_retry(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))
    buy_context = {"action": "BUY", "selected": {"strategy": "trend"}, "notional": 50.0, "timeframe": "1m", "decision_candle_close": NOON.isoformat()}
    await engine.execute_buy("u1", "BTC-USD", 50.0, buy_context)
    position = dict(db.positions_v2.docs[0])

    real_sell = engine.execution.sell
    calls = []

    async def sell(symbol, units, idempotency_key=None):
        order = await real_sell(symbol, units, idempotency_key=idempotency_key)
        calls.append(order["client_order_id"])
        if len(calls) == 1:
            return {**order, "success": False, "status": "rejected"}
        return order

    monkeypatch.setattr(engine.execution, "sell", sell)
    sell_context = {"action": "SELL", "selected": {"strategy": "trend"}, "timeframe": "1m", "decision_candle_close": (NOON + timedelta(minutes=1)).isoformat()}
    await engine.execute_sell("u1", "BTC-USD", position, sell_context)
    await engine.execute_sell("u1", "BTC-USD", position, sell_context)

    assert calls[0] == calls[1]
    sells = [t for t in db.trades_v2.docs if t["side"] == "SELL"]
    assert [t["status"] for t in sells] == ["rejected", "filled"]
    assert "realized_pnl" not in sells[0]
    assert "realized_pnl" in sells[1]


def claim_engine(monkeypatch, clock_at=NOON):
    """An engine on a FakeDB that enforces the uniq_trade_idempotency_claim partial index."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.trades_v2.add_unique("idempotency_key", partial={"idempotency_claim": True})
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: clock_at))
    return db, engine


def buy_context(at=NOON):
    return {"action": "BUY", "selected": {"strategy": "trend"}, "notional": 50.0, "timeframe": "1m", "decision_candle_close": at.isoformat()}


@pytest.mark.asyncio
async def test_a_rival_claim_stops_the_order_before_the_adapter(monkeypatch):
    """M1.5b task 15: the claim is taken before the adapter call, so a rival claim means no order is sent."""
    db, engine = claim_engine(monkeypatch)
    context = buy_context()
    key = ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", "BUY", context)
    real_acquire = engine.execution_control.acquire_lock

    async def acquire_after_a_rival_claims(user_id, symbol, side):
        # Another worker claims this decision between this engine's already_executed check and its lock.
        await db.trades_v2.insert_one({"user_id": user_id, "idempotency_key": key, "status": "pending", "idempotency_claim": True})
        return await real_acquire(user_id, symbol, side)

    monkeypatch.setattr(engine.execution_control, "acquire_lock", acquire_after_a_rival_claims)
    adapter = engine.execution.trading_service.adapter
    real_buy = adapter.place_market_buy
    adapter_calls = []

    async def spy(*args, **kwargs):
        adapter_calls.append(kwargs.get("client_order_id"))
        return await real_buy(*args, **kwargs)

    monkeypatch.setattr(adapter, "place_market_buy", spy)
    await engine.execute_buy("u1", "BTC-USD", 50.0, context)

    assert adapter_calls == []
    assert [(a["type"], a["severity"]) for a in db.alerts.docs] == [("duplicate_order_blocked", "warning")]
    assert db.positions_v2.docs == []
    assert db.ledger_entries.docs == []
    assert [t["status"] for t in db.trades_v2.docs] == ["pending"]
    assert db.execution_locks.docs == []  # the lock is released on the duplicate path


@pytest.mark.asyncio
async def test_a_rejected_attempt_frees_its_claim_for_a_retry_on_the_same_candle(monkeypatch):
    """M1.5b task 15: each attempt holds a pending claim when it reaches the adapter; a rejection frees it."""
    db, engine = claim_engine(monkeypatch)
    context = buy_context()
    key = ExecutionControlV2.build_idempotency_key("u1", "BTC-USD", "BUY", context)
    adapter = engine.execution.trading_service.adapter
    real_buy = adapter.place_market_buy
    claims_at_call = []

    async def reject_the_first(symbol, notional_usd, client_order_id=None):
        claims_at_call.append([t["status"] for t in db.trades_v2.docs if t.get("idempotency_key") == key and t.get("idempotency_claim")])
        order = await real_buy(symbol=symbol, notional_usd=notional_usd, client_order_id=client_order_id)
        if len(claims_at_call) == 1:
            return {**order, "success": False, "status": "rejected"}
        return order

    monkeypatch.setattr(adapter, "place_market_buy", reject_the_first)
    await engine.execute_buy("u1", "BTC-USD", 50.0, context)
    await engine.execute_buy("u1", "BTC-USD", 50.0, context)

    assert claims_at_call == [["pending"], ["pending"]]
    attempts = [(t["status"], t.get("idempotency_claim")) for t in db.trades_v2.docs if t.get("idempotency_key") == key]
    assert attempts == [("rejected", False), ("filled", True)]
    assert len(db.positions_v2.docs) == 1


@pytest.mark.asyncio
async def test_a_new_account_opens_on_the_engine_clock(monkeypatch):
    """M1.5b task 16: the account's first daily_start_date and starting_cash come from the engine clock, not wall time."""
    monkeypatch.setenv("TRADING_MODE", "paper")
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True, "symbols": ["BTC-USD"]})
    scripted = datetime(2031, 3, 4, 9, 0, tzinfo=UTC)
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: scripted))
    opened = []
    real_snapshot = engine.portfolio.update_risk_snapshot

    async def snapshot_spy(user_id, *args, **kwargs):
        if not opened:
            opened.append(await db.portfolio_state.find_one({"user_id": user_id}))
        return await real_snapshot(user_id, *args, **kwargs)

    monkeypatch.setattr(engine.portfolio, "update_risk_snapshot", snapshot_spy)
    monkeypatch.setattr(engine.planner, "build_plan", lambda symbol, prices, has_position, base_notional=100.0: {"symbol": symbol, "action": "HOLD"})
    await engine.cycle("u1")

    assert opened[0]["daily_start_date"] == "2031-03-04"
    assert opened[0].get("starting_cash") == 10000.0


@pytest.mark.asyncio
async def test_halt_stamps_the_engine_clock():
    """M1.5b task 16: halt_bot writes updated_at from the engine clock."""
    db = FakeDB()
    db.bot_configs.docs.append({"user_id": "u1", "is_active": True})
    scripted = datetime(2031, 3, 4, 9, 0, tzinfo=UTC)
    engine = BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: scripted))
    await engine.halt_bot("u1", "max drawdown")
    config = await db.bot_configs.find_one({"user_id": "u1"})
    assert config["is_active"] is False
    assert config["updated_at"] == scripted.isoformat()
