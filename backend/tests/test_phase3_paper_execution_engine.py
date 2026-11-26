import pytest

from services.execution_service_v2 import ExecutionServiceV2
from services.paper_execution_adapter_v2 import PaperExecutionAdapterV2, PaperExecutionConfig
from services.trading_service_v2 import TradingServiceV2


class FixedMarketData:
    def __init__(self, price=100.0, bid=None, ask=None):
        self.price = price
        self.bid = bid
        self.ask = ask

    async def get_current_price(self, symbol):
        return {"symbol": symbol, "price": self.price, "bid": self.bid, "ask": self.ask, "simulation": True}


@pytest.mark.asyncio
async def test_paper_market_buy_applies_fee_slippage_and_lifecycle():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=100.0, bid=99.5, ask=100.5),
        config=PaperExecutionConfig(
            fee_bps=10,
            min_notional_usd=5,
            partial_fill_threshold_usd=1000,
        ),
    )

    order = await adapter.place_market_buy("BTC-USD", 100.0, client_order_id="buy-1")

    assert order["success"] is True
    assert order["status"] == "filled"
    assert order["reference_price"] == 100.0
    assert order["filled_price"] == 100.5
    assert order["fee_usd"] == 0.1
    assert order["base_units"] == round(99.9 / 100.5, 12)
    assert order["paper_execution"] is True
    assert order["execution_adapter"] == "paper_v2"
    assert [event["status"] for event in order["lifecycle_events"]] == ["created", "accepted", "filled"]


@pytest.mark.asyncio
async def test_paper_market_sell_applies_fee_slippage_and_net_notional():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=100.0, bid=99.5, ask=100.5),
        config=PaperExecutionConfig(
            fee_bps=10,
            partial_fill_threshold_usd=1000,
        ),
    )

    order = await adapter.place_market_sell("BTC-USD", 1.0, client_order_id="sell-1")

    assert order["success"] is True
    assert order["status"] == "filled"
    assert order["reference_price"] == 100.0
    assert order["filled_price"] == 99.5
    assert order["gross_notional_usd"] == 99.5
    assert order["fee_usd"] == 0.0995
    assert order["notional_usd"] == 99.4005
    assert order["base_units"] == 1.0


@pytest.mark.asyncio
async def test_paper_market_buy_rejects_below_minimum_notional():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=100.0),
        config=PaperExecutionConfig(min_notional_usd=25.0),
    )

    order = await adapter.place_market_buy("BTC-USD", 10.0, client_order_id="too-small")

    assert order["success"] is False
    assert order["status"] == "rejected"
    assert order["reject_reason"] == "notional below minimum"
    assert order["base_units"] == 0.0


@pytest.mark.asyncio
async def test_paper_execution_partial_fills_large_orders():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=100.0, bid=99.99, ask=100.0),
        config=PaperExecutionConfig(
            fee_bps=0,
            partial_fill_threshold_usd=250.0,
            partial_fill_ratio=0.5,
        ),
    )

    order = await adapter.place_market_buy("BTC-USD", 1000.0, client_order_id="large")

    assert order["success"] is True
    assert order["status"] == "partially_filled"
    assert order["notional_usd"] == 500.0
    assert order["unfilled_notional_usd"] == 500.0
    assert order["base_units"] == 5.0
    assert order["fill_ratio"] == 0.5


@pytest.mark.asyncio
async def test_paper_execution_rejects_unsupported_symbol():
    adapter = PaperExecutionAdapterV2(market_data=FixedMarketData(price=100.0))

    order = await adapter.place_market_buy("DOGE-USD", 100.0, client_order_id="unsupported")

    assert order["success"] is False
    assert order["status"] == "rejected"
    assert order["reject_reason"] == "unsupported symbol"


@pytest.mark.asyncio
async def test_trading_service_delegates_to_paper_adapter():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=200.0, bid=199.9, ask=200.0),
        config=PaperExecutionConfig(fee_bps=0, partial_fill_threshold_usd=1000),
    )
    service = TradingServiceV2(adapter=adapter)

    order = await service.place_market_buy("ETH-USD", 100.0, client_order_id="delegated")

    assert order["success"] is True
    assert order["filled_price"] == 200.0
    assert order["base_units"] == 0.5
    assert order["paper_execution"] is True


@pytest.mark.asyncio
async def test_execution_service_preserves_contract_and_stable_client_order_id():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=100.0, bid=99.99, ask=100.0),
        config=PaperExecutionConfig(fee_bps=0, partial_fill_threshold_usd=1000),
    )
    service = ExecutionServiceV2()
    service.trading_service = TradingServiceV2(adapter=adapter)

    first = await service.buy("BTC-USD", 50.0, idempotency_key="same-key")
    second = await service.buy("BTC-USD", 50.0, idempotency_key="same-key")

    assert first["client_order_id"] == second["client_order_id"]
    assert first["idempotency_key"] == "same-key"
    assert first["requested_notional_usd"] == 50.0
    assert first["paper_execution"] is True


@pytest.mark.asyncio
async def test_paper_buy_fills_at_ask():
    adapter = PaperExecutionAdapterV2(market_data=FixedMarketData(price=100.0, bid=99.9, ask=100.1))
    order = await adapter.place_market_buy("BTC-USD", 50.0)
    assert order["filled_price"] == 100.1
    assert order["price_basis"] == "quote"


@pytest.mark.asyncio
async def test_paper_sell_fills_at_bid():
    adapter = PaperExecutionAdapterV2(market_data=FixedMarketData(price=100.0, bid=99.9, ask=100.1))
    order = await adapter.place_market_sell("BTC-USD", 1.0)
    assert order["filled_price"] == 99.9
    assert order["price_basis"] == "quote"


@pytest.mark.asyncio
async def test_paper_fill_uses_synthetic_spread_without_quote():
    adapter = PaperExecutionAdapterV2(market_data=FixedMarketData(price=100.0))
    buy = await adapter.place_market_buy("BTC-USD", 50.0)
    sell = await adapter.place_market_sell("BTC-USD", 1.0)
    assert buy["filled_price"] == pytest.approx(100.0 * 1.0001)
    assert sell["filled_price"] == pytest.approx(100.0 * 0.9999)
    assert buy["price_basis"] == sell["price_basis"] == "synthetic_spread"


@pytest.mark.asyncio
@pytest.mark.parametrize("bid,ask", [(100.1, 99.9), (100.0, 100.0), (0.0, 100.1), (99.9, -1.0)])
async def test_paper_fill_treats_unusable_quote_as_missing(bid, ask):
    adapter = PaperExecutionAdapterV2(market_data=FixedMarketData(price=100.0, bid=bid, ask=ask))
    order = await adapter.place_market_buy("BTC-USD", 50.0)
    assert order["filled_price"] == pytest.approx(100.0 * 1.0001)
    assert order["price_basis"] == "synthetic_spread"


@pytest.mark.asyncio
async def test_extra_slippage_is_optional_and_small(monkeypatch):
    monkeypatch.delenv("PAPER_EXTRA_SLIPPAGE_BPS", raising=False)
    quote = FixedMarketData(price=100.0, bid=99.9, ask=100.1)
    default = PaperExecutionAdapterV2(market_data=quote, config=PaperExecutionAdapterV2.from_env())
    assert (await default.place_market_buy("BTC-USD", 50.0))["filled_price"] == 100.1

    monkeypatch.setenv("PAPER_EXTRA_SLIPPAGE_BPS", "1")
    adapter = PaperExecutionAdapterV2(market_data=quote, config=PaperExecutionAdapterV2.from_env())
    assert (await adapter.place_market_buy("BTC-USD", 50.0))["filled_price"] == pytest.approx(100.1 * 1.0001)
    assert (await adapter.place_market_sell("BTC-USD", 1.0))["filled_price"] == pytest.approx(99.9 * 0.9999)
