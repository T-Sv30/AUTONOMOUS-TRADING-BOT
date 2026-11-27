import os

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-route-model-import")

import pytest

from api_routes_v2 import BotConfigUpdate
from services.ledger_service_v2 import LedgerServiceV2
from services.portfolio_service_v2 import PortfolioServiceV2


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        reverse = direction == -1
        self.docs = sorted(self.docs, key=lambda doc: doc.get(key, ""), reverse=reverse)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def find_one(self, query, projection=None, sort=None):
        matches = [doc for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        if sort and matches:
            key, direction = sort[0]
            matches = sorted(matches, key=lambda doc: doc.get(key, ""), reverse=direction == -1)
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
        self.ledger_entries = FakeCollection()
        self.reconciliation_reports = FakeCollection()
        self.portfolio_state = FakeCollection()
        self.positions_v2 = FakeCollection()
        self.trades_v2 = FakeCollection()
        self.risk_metrics = FakeCollection()
        self.bot_configs = FakeCollection()


@pytest.mark.asyncio
async def test_ledger_records_buy_fill_and_rebuilds_state():
    db = FakeDB()
    ledger = LedgerServiceV2(db)

    await ledger.record_buy_fill(
        "user-1",
        "BTC-USD",
        filled_price=100.0,
        base_units=1.0,
        notional_usd=100.0,
        fee_usd=0.1,
        order={"order_id": "order-1", "client_order_id": "client-1"},
    )
    rebuilt = await ledger.rebuild_from_ledger("user-1", starting_cash=1000.0)

    assert rebuilt["cash_balance"] == 900.0
    assert rebuilt["ledger_entries"] == 2
    assert rebuilt["positions"] == [
        {
            "symbol": "BTC-USD",
            "base_units": 1.0,
            "notional_usd": 100.0,
            "avg_price": 100.0,
            "fees_paid_usd": 0.1,
        }
    ]


@pytest.mark.asyncio
async def test_ledger_records_sell_fill_and_realized_pnl():
    db = FakeDB()
    ledger = LedgerServiceV2(db)

    await ledger.record_buy_fill("user-1", "BTC-USD", 100.0, 1.0, 100.0, 0.0)
    await ledger.record_sell_fill(
        "user-1",
        "BTC-USD",
        filled_price=120.0,
        base_units=1.0,
        gross_proceeds=120.0,
        net_proceeds=119.9,
        fee_usd=0.1,
        sold_cost_basis=100.0,
        realized_pnl=19.9,
    )
    rebuilt = await ledger.rebuild_from_ledger("user-1", starting_cash=1000.0)

    assert rebuilt["cash_balance"] == 1019.9
    assert rebuilt["realized_pnl"] == 19.9
    assert rebuilt["positions"] == []


@pytest.mark.asyncio
async def test_reconciliation_reports_ok_when_state_matches_ledger():
    db = FakeDB()
    portfolio = PortfolioServiceV2(db)

    await portfolio.ensure_account_state("user-1", starting_cash=1000.0)
    await portfolio.record_buy_fill(
        "user-1",
        "ETH-USD",
        filled_price=50.0,
        base_units=2.0,
        notional_usd=100.0,
        fee_usd=0.0,
        order={"order_id": "buy-1", "client_order_id": "client-buy-1"},
    )
    report = await portfolio.reconcile_with_ledger("user-1", starting_cash=1000.0)

    assert report["status"] == "ok"
    assert report["issues"] == []
    assert len(db.reconciliation_reports.docs) == 1


@pytest.mark.asyncio
async def test_reconciliation_reports_ok_with_nonzero_buy_fee():
    db = FakeDB()
    portfolio = PortfolioServiceV2(db)

    await portfolio.ensure_account_state("user-1", starting_cash=1000.0)
    await portfolio.record_buy_fill(
        "user-1",
        "BTC-USD",
        filled_price=100.0,
        base_units=1.0,
        notional_usd=100.0,
        fee_usd=0.1,
        order={"order_id": "buy-fee-1", "client_order_id": "client-buy-fee-1"},
    )

    state = await db.portfolio_state.find_one({"user_id": "user-1"}, {"_id": 0})
    position = await db.positions_v2.find_one({"user_id": "user-1", "symbol": "BTC-USD"}, {"_id": 0})
    report = await portfolio.reconcile_with_ledger("user-1", starting_cash=1000.0)

    assert state["cash_balance"] == 900.0
    assert position["notional_usd"] == 100.0
    assert position["fees_paid_usd"] == 0.1
    assert report["status"] == "ok"
    assert report["issues"] == []


@pytest.mark.asyncio
async def test_reconciliation_detects_cash_and_position_mismatches():
    db = FakeDB()
    portfolio = PortfolioServiceV2(db)

    await portfolio.ensure_account_state("user-1", starting_cash=1000.0)
    await portfolio.record_buy_fill("user-1", "BTC-USD", 100.0, 1.0, 100.0, 0.0)
    await db.portfolio_state.update_one({"user_id": "user-1"}, {"$inc": {"cash_balance": 10.0}}, upsert=True)
    await db.positions_v2.update_one({"user_id": "user-1", "symbol": "BTC-USD"}, {"$set": {"base_units": 1.5}}, upsert=True)

    report = await portfolio.reconcile_with_ledger("user-1", starting_cash=1000.0)

    assert report["status"] == "mismatch"
    issue_types = {issue["type"] for issue in report["issues"]}
    assert "cash_mismatch" in issue_types
    assert "position_mismatch" in issue_types


@pytest.mark.asyncio
async def test_portfolio_records_ledger_entries_for_buy_and_sell_fills():
    db = FakeDB()
    portfolio = PortfolioServiceV2(db)

    await portfolio.ensure_account_state("user-1", starting_cash=1000.0)
    await portfolio.record_buy_fill("user-1", "BTC-USD", 100.0, 1.0, 100.0, 0.0, order={"order_id": "b1"})
    result = await portfolio.record_sell_fill("user-1", "BTC-USD", 120.0, 1.0, 0.0, order={"order_id": "s1"})
    entries = await LedgerServiceV2(db).list_entries("user-1", limit=20)

    assert result["realized_pnl"] == 20.0
    event_types = {entry["event_type"] for entry in entries}
    assert "BUY_FILL" in event_types
    assert "SELL_FILL" in event_types
    assert "REALIZED_PNL" in event_types
    assert len(entries) == 5


def test_bot_config_update_payload_does_not_require_client_user_id():
    # The route sets user_id from the token; client user_id and is_active keys are ignored (M1.5b task 12).
    config = BotConfigUpdate(user_id="someone-else", is_active=True, symbols=["BTC-USD"])

    assert config.model_dump(exclude_unset=True) == {"symbols": ["BTC-USD"]}
