import pytest

from services.portfolio_service_v2 import PortfolioServiceV2


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

    async def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        return dict(matches[0]) if matches else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        doc = None
        for existing in self.docs:
            if all(existing.get(k) == v for k, v in query.items()):
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
        self.docs = [doc for doc in self.docs if not all(doc.get(k) == v for k, v in query.items())]

    def find(self, query, projection=None):
        matches = [dict(doc) for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        return FakeCursor(matches)

    async def count_documents(self, query):
        return len([doc for doc in self.docs if all(doc.get(k) == v for k, v in query.items())])


class FakeDB:
    def __init__(self):
        self.portfolio_state = FakeCollection()
        self.positions_v2 = FakeCollection()
        self.trades_v2 = FakeCollection()
        self.risk_metrics = FakeCollection()
        self.bot_configs = FakeCollection()

    def __getattr__(self, name):
        # Services gained collections after this fake was written; create them on demand.
        if name.startswith("_"):
            raise AttributeError(name)
        collection = FakeCollection()
        setattr(self, name, collection)
        return collection


@pytest.mark.asyncio
async def test_buy_and_sell_accounting_includes_fees_and_writes_risk_snapshot():
    db = FakeDB()
    service = PortfolioServiceV2(db)
    user_id = "user-1"

    await service.record_buy_fill(
        user_id=user_id,
        symbol="BTC-USD",
        filled_price=100.0,
        base_units=1.0,
        notional_usd=100.0,
        fee_usd=1.0,
    )

    state = await db.portfolio_state.find_one({"user_id": user_id})
    position = await db.positions_v2.find_one({"user_id": user_id, "symbol": "BTC-USD"})
    # notional_usd is fee-inclusive (paper fills buy base units with notional - fee),
    # so cash and cost basis move by the notional only (behavior since f42585a).
    assert state["cash_balance"] == 9900.0
    assert position["notional_usd"] == 100.0
    assert position["avg_price"] == 100.0

    result = await service.record_sell_fill(
        user_id=user_id,
        symbol="BTC-USD",
        filled_price=110.0,
        base_units=1.0,
        fee_usd=1.0,
    )

    state = await db.portfolio_state.find_one({"user_id": user_id})
    assert state["cash_balance"] == 10009.0
    assert result["realized_pnl"] == 9.0

    snapshot = await service.update_risk_snapshot(user_id)
    assert snapshot["total_equity"] == 10009.0
    assert len(db.risk_metrics.docs) == 1


@pytest.mark.asyncio
async def test_trade_ledger_records_attempts():
    db = FakeDB()
    service = PortfolioServiceV2(db)

    await service.record_trade_attempt(
        user_id="user-1",
        symbol="ETH-USD",
        side="BUY",
        order={
            "status": "filled",
            "order_id": "order-1",
            "client_order_id": "client-1",
            "notional_usd": 50.0,
            "base_units": 0.02,
            "filled_price": 2500.0,
            "fee_usd": 0.05,
            "simulation": True,
        },
        context={"strategy": "test"},
    )

    trades = await service.get_trades("user-1")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "ETH-USD"
    assert trades[0]["fee_usd"] == 0.05
    assert trades[0]["signal_snapshot"] == {"strategy": "test"}
