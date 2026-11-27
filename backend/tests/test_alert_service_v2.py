"""M1.5b task 16: repeated alerts are counted on one open alert instead of flooding the collection."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeDB

from services.alert_service import AlertService
from services.bot_engine import BotEngine
from services.market_data_service import MarketDataService, MarketDataUnavailable

NOON = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def engine_on(db):
    return BotEngine(db, market=MarketDataService(db=db, source="simulated", clock=lambda: NOON))


@pytest.mark.asyncio
async def test_ten_market_data_failures_in_a_minute_leave_one_alert_with_count_ten():
    db = FakeDB()
    engine = engine_on(db)
    for _ in range(10):
        await engine.report_market_data_failure("u1", "BTC-USD", MarketDataUnavailable("feed down"))

    assert len(db.alerts.docs) == 1
    alert = db.alerts.docs[0]
    assert alert["type"] == "market_data_failed"
    assert alert["count"] == 10
    assert alert["last_seen_at"] >= alert["created_at"]


@pytest.mark.asyncio
async def test_failures_for_different_symbols_are_counted_separately():
    db = FakeDB()
    engine = engine_on(db)
    for _ in range(5):
        await engine.report_market_data_failure("u1", "BTC-USD", MarketDataUnavailable("feed down"))
        await engine.report_market_data_failure("u1", "ETH-USD", MarketDataUnavailable("feed down"))

    counts = sorted((a["context"]["symbol"], a.get("count")) for a in db.alerts.docs)
    assert counts == [("BTC-USD", 5), ("ETH-USD", 5)]


@pytest.mark.asyncio
async def test_an_alert_older_than_the_window_is_not_reused():
    db = FakeDB()
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    db.alerts.docs.append({"user_id": "u1", "type": "market_data_failed", "context": {"symbol": "BTC-USD"}, "acknowledged": False, "created_at": old, "last_seen_at": old, "count": 1})
    await engine_on(db).report_market_data_failure("u1", "BTC-USD", MarketDataUnavailable("feed down"))

    assert [a.get("count") for a in db.alerts.docs] == [1, 1]


@pytest.mark.asyncio
async def test_an_acknowledged_alert_is_not_reused():
    db = FakeDB()
    recent = datetime.now(UTC).isoformat()
    db.alerts.docs.append({"user_id": "u1", "type": "market_data_failed", "context": {"symbol": "BTC-USD"}, "acknowledged": True, "created_at": recent, "last_seen_at": recent, "count": 1})
    await engine_on(db).report_market_data_failure("u1", "BTC-USD", MarketDataUnavailable("feed down"))

    assert [(a["acknowledged"], a.get("count")) for a in db.alerts.docs] == [(True, 1), (False, 1)]


@pytest.mark.asyncio
async def test_another_users_alert_is_not_reused():
    db = FakeDB()
    engine = engine_on(db)
    await engine.report_market_data_failure("u1", "BTC-USD", MarketDataUnavailable("feed down"))
    await engine.report_market_data_failure("u2", "BTC-USD", MarketDataUnavailable("feed down"))

    assert sorted((a["user_id"], a.get("count")) for a in db.alerts.docs) == [("u1", 1), ("u2", 1)]


@pytest.mark.asyncio
async def test_emit_without_a_window_always_inserts():
    db = FakeDB()
    service = AlertService(db)
    first = await service.emit("u1", "bot_halted", "critical", "halted", {"reason": "risk"})
    await service.emit("u1", "bot_halted", "critical", "halted", {"reason": "risk"})

    assert len(db.alerts.docs) == 2
    assert "_id" not in first
