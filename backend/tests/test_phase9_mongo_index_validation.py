import pytest

from services.mongo_indexes_v2 import MongoIndexServiceV2


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.indexes = []

    async def create_index(self, keys, **kwargs):
        record = {"collection": self.name, "keys": keys, **kwargs}
        self.indexes.append(record)
        return kwargs.get("name", f"{self.name}_index_{len(self.indexes)}")


class FakeDB:
    def __init__(self):
        collections = [
            "market_candles",
            "trades_v2",
            "positions_v2",
            "portfolio_state",
            "ledger_entries",
            "reconciliation_reports",
            "live_readonly_reports",
            "live_readonly_snapshots",
            "live_order_audits",
            "live_approval_challenges",
            "live_order_transitions",
            "live_post_submit_reconciliation_requirements",
            "manual_live_pilot_reports",
            "manual_live_pilot_signoffs",
            "live_halts",
            "worker_heartbeats",
            "bot_ownership",
            "execution_locks",
            "alerts",
            "auth_failures",
        ]
        for name in collections:
            setattr(self, name, FakeCollection(name))


@pytest.mark.asyncio
async def test_phase9_mongo_indexes_include_live_pilot_report_and_signoff_indexes():
    db = FakeDB()

    result = await MongoIndexServiceV2(db).ensure_indexes()

    names = set(result["created_or_verified"])
    assert "manual_live_pilot_reports_user_generated" in names
    assert "uniq_manual_live_pilot_report_order" in names
    assert "uniq_manual_live_pilot_report_hash" in names
    assert "manual_live_pilot_signoffs_user_signed" in names
    assert "uniq_manual_live_pilot_signoff_order" in names
    assert "uniq_manual_live_pilot_signoff_hash" in names
    assert "live_post_submit_reconciliation_user_status" in names
    assert "uniq_live_post_submit_reconciliation_order" in names


def test_phase9_mongo_index_uniqueness_for_report_and_signoff_records():
    db = FakeDB()
    service = MongoIndexServiceV2(db)

    async def run():
        await service.ensure_indexes()

    import asyncio
    asyncio.run(run())

    report_unique = [idx for idx in db.manual_live_pilot_reports.indexes if idx.get("name") == "uniq_manual_live_pilot_report_order"]
    signoff_unique = [idx for idx in db.manual_live_pilot_signoffs.indexes if idx.get("name") == "uniq_manual_live_pilot_signoff_order"]

    assert report_unique and report_unique[0].get("unique") is True
    assert signoff_unique and signoff_unique[0].get("unique") is True


@pytest.mark.asyncio
async def test_trade_idempotency_claim_index_is_unique_and_partial():
    db = FakeDB()
    await MongoIndexServiceV2(db).ensure_indexes()

    claim = [idx for idx in db.trades_v2.indexes if idx.get("name") == "uniq_trade_idempotency_claim"]
    assert claim, "uniq_trade_idempotency_claim index missing"
    assert claim[0]["keys"] == [("idempotency_key", 1)]
    assert claim[0].get("unique") is True
    assert claim[0].get("partialFilterExpression") == {"idempotency_claim": True}
