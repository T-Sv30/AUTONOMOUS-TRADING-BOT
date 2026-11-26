import pytest

from services.execution_service_v2 import ExecutionServiceV2
from services.paper_execution_adapter_v2 import PaperExecutionAdapterV2, PaperExecutionConfig
from services.trading_service_v2 import TradingServiceV2


class FixedMarketData:
    def __init__(self, price=100.0):
        self.price = price

    async def get_current_price(self, symbol):
        return {"symbol": symbol, "price": self.price, "simulation": True}


@pytest.mark.asyncio
async def test_paper_market_buy_applies_fee_slippage_and_lifecycle():
    adapter = PaperExecutionAdapterV2(
        market_data=FixedMarketData(price=100.0),
        config=PaperExecutionConfig(
            fee_bps=10,
            buy_slippage_bps=50,
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
        market_data=FixedMarketData(price=100.0),
        config=PaperExecutionConfig(
            fee_bps=10,
            sell_slippage_bps=50,
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
        market_data=FixedMarketData(price=100.0),
        config=PaperExecutionConfig(
            fee_bps=0,
            buy_slippage_bps=0,
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
        market_data=FixedMarketData(price=200.0),
        config=PaperExecutionConfig(fee_bps=0, buy_slippage_bps=0, partial_fill_threshold_usd=1000),
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
        market_data=FixedMarketData(price=100.0),
        config=PaperExecutionConfig(fee_bps=0, buy_slippage_bps=0, partial_fill_threshold_usd=1000),
    )
    service = ExecutionServiceV2()
    service.trading_service = TradingServiceV2(adapter=adapter)

    first = await service.buy("BTC-USD", 50.0, idempotency_key="same-key")
    second = await service.buy("BTC-USD", 50.0, idempotency_key="same-key")

    assert first["client_order_id"] == second["client_order_id"]
    assert first["idempotency_key"] == "same-key"
    assert first["requested_notional_usd"] == 50.0
    assert first["paper_execution"] is True
