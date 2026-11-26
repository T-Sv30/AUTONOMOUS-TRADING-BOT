from datetime import UTC, datetime, timedelta

import pytest

from services.market_data_service import MarketDataService, MarketDataUnavailable

NOON = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def sim(at):
    return MarketDataService(source="simulated", seed="s", clock=lambda: at)


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *args, **kwargs):
        self.docs = sorted(self.docs, key=lambda doc: doc.get("open_time", ""), reverse=True)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            inserted = dict(query)
            inserted.update(update.get("$set", {}))
            self.docs.append(inserted)

    def find(self, query, projection=None):
        matches = [dict(doc) for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        return FakeCursor(matches)


class FakeDB:
    def __init__(self):
        self.market_candles = FakeCollection()


def test_timeframe_granularity_validation():
    assert MarketDataService.granularity_for("1m") == 60
    assert MarketDataService.granularity_for("5m") == 300
    assert MarketDataService.granularity_for("15m") == 900
    assert MarketDataService.granularity_for("1h") == 3600
    assert MarketDataService.granularity_for("1d") == 86400
    with pytest.raises(ValueError):
        MarketDataService.granularity_for("2m")


@pytest.mark.asyncio
async def test_seeded_simulation_candles_are_deterministic_for_prices():
    first = MarketDataService(source="simulated", seed="same-seed", clock=lambda: NOON)
    second = MarketDataService(source="simulated", seed="same-seed", clock=lambda: NOON)

    first_candles = await first.get_candles("BTC-USD", timeframe="5m", periods=5)
    second_candles = await second.get_candles("BTC-USD", timeframe="5m", periods=5)

    assert [c["close"] for c in first_candles] == [c["close"] for c in second_candles]
    assert [c["volume"] for c in first_candles] == [c["volume"] for c in second_candles]
    assert all(c["simulation"] is True for c in first_candles)
    assert all(c["source"] == "seeded_simulation" for c in first_candles)
    assert all(c["quality"] == "synthetic_seeded" for c in first_candles)


@pytest.mark.asyncio
async def test_candles_are_persisted_and_retrievable_from_db():
    db = FakeDB()
    service = MarketDataService(db=db, source="simulated", seed="persist-seed")

    candles = await service.get_candles("ETH-USD", timeframe="15m", periods=3)
    stored = await service.get_stored_candles("ETH-USD", timeframe="15m", limit=10)

    assert len(candles) == 3
    assert len(stored) == 3
    assert [c["open_time"] for c in stored] == [c["open_time"] for c in candles]
    assert all(c["checksum"] for c in stored)


@pytest.mark.asyncio
async def test_get_historical_data_returns_legacy_price_shape_with_metadata():
    service = MarketDataService(source="simulated", seed="legacy-shape")
    history = await service.get_historical_data("BTC-USD", periods=4, timeframe="1h")

    assert len(history) == 4
    assert set(["timestamp", "price", "volume", "simulation", "source", "timeframe", "quality"]).issubset(history[0].keys())
    assert history[0]["timeframe"] == "1h"


@pytest.mark.asyncio
async def test_stored_candles_requires_database_handle():
    service = MarketDataService(source="simulated")
    with pytest.raises(MarketDataUnavailable):
        await service.get_stored_candles("BTC-USD")


def test_market_data_service_reads_source_from_env(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_SOURCE", "replay")
    assert MarketDataService().source == "replay"


@pytest.mark.asyncio
async def test_simulated_ticker_moves_over_time():
    first = await sim(NOON).get_current_price("BTC-USD")
    later = await sim(NOON + timedelta(hours=1)).get_current_price("BTC-USD")
    assert first["price"] != later["price"]


@pytest.mark.asyncio
async def test_simulation_is_deterministic_for_same_instant():
    a = await sim(NOON).get_candles("BTC-USD", periods=50)
    b = await sim(NOON).get_candles("BTC-USD", periods=50)
    assert [c["close"] for c in a] == [c["close"] for c in b]


@pytest.mark.asyncio
async def test_simulated_ticker_agrees_with_last_candle():
    svc = sim(NOON)
    ticker = await svc.get_current_price("BTC-USD")
    last = (await svc.get_candles("BTC-USD", periods=5))[-1]
    assert abs(ticker["price"] - last["close"]) / last["close"] < 0.005


@pytest.mark.asyncio
async def test_overlapping_windows_share_candles():
    a = await sim(NOON).get_candles("BTC-USD", periods=60)
    b = await sim(NOON + timedelta(minutes=10)).get_candles("BTC-USD", periods=60)
    shared = {c["open_time"]: c["close"] for c in a}
    assert all(shared[c["open_time"]] == c["close"] for c in b if c["open_time"] in shared)


@pytest.mark.asyncio
async def test_simulated_ticker_has_bid_below_ask():
    t = await sim(NOON).get_current_price("BTC-USD")
    assert t["bid"] < t["price"] < t["ask"]
