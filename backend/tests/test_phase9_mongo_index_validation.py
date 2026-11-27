from datetime import datetime

import pytest
from fakes import FakeDB as SharedFakeDB
from pymongo.errors import OperationFailure

from services.mongo_indexes_v2 import MongoIndexServiceV2
from services.portfolio_service_v2 import PortfolioServiceV2


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.indexes = []
        self.dropped = []
        self.existing = set()

    async def create_index(self, keys, **kwargs):
        record = {"collection": self.name, "keys": keys, **kwargs}
        self.indexes.append(record)
        return kwargs.get("name", f"{self.name}_index_{len(self.indexes)}")

    async def drop_index(self, name):
        if name not in self.existing:
            raise OperationFailure(f"index not found with name [{name}]", code=27)
        self.existing.discard(name)
        self.dropped.append(name)


class FakeDB:
    """Collections appear on demand, so new indexes need no list update here."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        collection = FakeCollection(name)
        setattr(self, name, collection)
        return collection


def index_named(collection, name):
    matches = [idx for idx in collection.indexes if idx.get("name") == name]
    assert matches, f"{name} index missing on {collection.name}"
    return matches[0]


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


# M1.5b task 7 (P0-10): TTL indexes keyed on real datetimes; a failed index never crashes startup.

@pytest.mark.asyncio
async def test_ttl_indexes_use_datetime_fields():
    db = FakeDB()
    await MongoIndexServiceV2(db).ensure_indexes()

    auth_ttl = index_named(db.auth_failures, "auth_failures_ttl_ts")
    assert auth_ttl["keys"] == [("created_at_ts", 1)]
    assert auth_ttl["expireAfterSeconds"] == 3600
    challenge_ttl = index_named(db.live_approval_challenges, "live_approval_expiry_ttl_ts")
    assert challenge_ttl["keys"] == [("expires_at_ts", 1)]
    assert challenge_ttl["expireAfterSeconds"] == 0
    # The old TTL indexes sat on ISO strings, which Mongo never expires.
    assert not [idx for idx in db.auth_failures.indexes if idx.get("name") == "auth_failures_ttl"]
    assert not [idx for idx in db.live_approval_challenges.indexes if idx.get("name") == "live_approval_expiry_ttl"]


@pytest.mark.asyncio
async def test_ensure_indexes_drops_the_old_string_ttl_indexes_by_name():
    db = FakeDB()
    db.auth_failures.existing.add("auth_failures_ttl")
    db.live_approval_challenges.existing.add("live_approval_expiry_ttl")

    result = await MongoIndexServiceV2(db).ensure_indexes()

    assert db.auth_failures.dropped == ["auth_failures_ttl"]
    assert db.live_approval_challenges.dropped == ["live_approval_expiry_ttl"]
    assert result.get("errors") == []


@pytest.mark.asyncio
async def test_ensure_indexes_reports_a_failed_index_instead_of_raising():
    db = SharedFakeDB()
    original = db.market_candles.create_index

    async def failing_create_index(keys, **kwargs):
        if kwargs.get("name") == "uniq_market_candle":
            raise OperationFailure("E11000 duplicate key error collection: market_candles", code=11000)
        return await original(keys, **kwargs)

    db.market_candles.create_index = failing_create_index

    result = await MongoIndexServiceV2(db).ensure_indexes()

    assert result["errors"] == [{"name": "uniq_market_candle", "error": "E11000 duplicate key error collection: market_candles"}]
    assert "uniq_market_candle" not in result["created_or_verified"]
    # Every later index is still created.
    assert "auth_failures_email_created" in result["created_or_verified"]


@pytest.mark.asyncio
async def test_clean_database_reports_no_index_errors():
    result = await MongoIndexServiceV2(SharedFakeDB()).ensure_indexes()
    assert result["status"] == "ok"
    assert result.get("errors") == []


# M1.5b task 8 (P0-11): core indexes, unique users and bot configs, risk_metrics TTL.

@pytest.mark.asyncio
async def test_core_user_config_and_risk_metrics_indexes(monkeypatch):
    monkeypatch.delenv("RISK_METRICS_RETENTION_DAYS", raising=False)
    db = FakeDB()
    await MongoIndexServiceV2(db).ensure_indexes()

    email = index_named(db.users, "uniq_users_email")
    assert email["keys"] == [("email", 1)] and email.get("unique") is True
    user_id = index_named(db.users, "uniq_users_id")
    assert user_id["keys"] == [("id", 1)] and user_id.get("unique") is True
    config = index_named(db.bot_configs, "uniq_bot_config_user")
    assert config["keys"] == [("user_id", 1)] and config.get("unique") is True
    by_time = index_named(db.risk_metrics, "risk_metrics_user_timestamp")
    assert by_time["keys"] == [("user_id", 1), ("timestamp", -1)]
    ttl = index_named(db.risk_metrics, "risk_metrics_ttl")
    assert ttl["keys"] == [("created_at_ts", 1)]
    assert ttl["expireAfterSeconds"] == 30 * 86400


@pytest.mark.asyncio
async def test_risk_metrics_retention_comes_from_env(monkeypatch):
    monkeypatch.setenv("RISK_METRICS_RETENTION_DAYS", "7")
    db = FakeDB()
    await MongoIndexServiceV2(db).ensure_indexes()
    assert index_named(db.risk_metrics, "risk_metrics_ttl")["expireAfterSeconds"] == 7 * 86400


@pytest.mark.asyncio
async def test_risk_snapshot_stores_a_datetime_for_the_ttl_index():
    db = SharedFakeDB()
    await PortfolioServiceV2(db).update_risk_snapshot("user-ttl")
    stored = db.risk_metrics.docs[-1]
    assert isinstance(stored.get("created_at_ts"), datetime)
    assert stored["created_at_ts"].tzinfo is not None


# M1.5b task 11 (P0-19): the audit chain cannot fork.

@pytest.mark.asyncio
async def test_audit_chain_unique_indexes_replace_the_non_unique_one():
    db = FakeDB()
    db.live_order_audits.existing.add("live_audits_user_previous_hash")

    result = await MongoIndexServiceV2(db).ensure_indexes()

    sequence = index_named(db.live_order_audits, "uniq_live_audit_user_sequence")
    assert sequence["keys"] == [("user_id", 1), ("sequence", 1)]
    assert sequence.get("unique") is True
    previous = index_named(db.live_order_audits, "uniq_live_audit_user_previous_hash")
    assert previous["keys"] == [("user_id", 1), ("previous_hash", 1)]
    assert previous.get("unique") is True
    assert db.live_order_audits.dropped == ["live_audits_user_previous_hash"]
    assert not [idx for idx in db.live_order_audits.indexes if idx.get("name") == "live_audits_user_previous_hash"]
    assert result.get("errors") == []
