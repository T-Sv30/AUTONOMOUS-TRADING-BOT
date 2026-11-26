import pytest

import api_routes_v2
from services.paper_execution_adapter_v2 import PaperExecutionAdapterV2


class ClosableMarket:
    def __init__(self, fail=False):
        self.closed = False
        self.fail = fail

    async def close(self):
        self.closed = True

    async def get_current_price(self, symbol):
        if self.fail:
            raise RuntimeError("boom")
        return {"symbol": symbol, "price": 1.0}


@pytest.mark.asyncio
async def test_adapter_closes_market_data_it_created(monkeypatch):
    owned = ClosableMarket()
    monkeypatch.setattr("services.paper_execution_adapter_v2.MarketDataService", lambda: owned)
    await PaperExecutionAdapterV2().close()
    assert owned.closed


@pytest.mark.asyncio
async def test_adapter_leaves_injected_market_data_open():
    injected = ClosableMarket()
    await PaperExecutionAdapterV2(market_data=injected).close()
    assert not injected.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_market_data_route_closes_its_per_request_service(monkeypatch, fail):
    svc = ClosableMarket(fail=fail)
    monkeypatch.setattr(api_routes_v2, "MarketDataService", lambda **kwargs: svc)
    try:
        await api_routes_v2.get_market_data("BTC-USD", current_user={"id": "u"})
    except Exception:
        assert fail
    assert svc.closed


@pytest.mark.asyncio
async def test_market_data_route_gives_the_service_the_database(monkeypatch):
    seen = []

    def factory(*args, **kwargs):
        seen.append(kwargs)
        return ClosableMarket()

    monkeypatch.setattr(api_routes_v2, "MarketDataService", factory)
    await api_routes_v2.get_market_data("BTC-USD", current_user={"id": "u"})
    assert seen == [{"db": api_routes_v2.db}]
