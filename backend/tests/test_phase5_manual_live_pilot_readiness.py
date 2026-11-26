from datetime import UTC, datetime, timedelta

import pytest

from services.manual_live_pilot_readiness_service_v2 import ManualLivePilotReadinessServiceV2


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda doc: doc.get(key, ""), reverse=direction == -1)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def find_one(self, query, projection=None, sort=None):
        def match(doc):
            return all(doc.get(key) == value for key, value in query.items())

        matches = [dict(doc) for doc in self.docs if match(doc)]
        if sort:
            for key, direction in reversed(sort):
                matches = sorted(matches, key=lambda doc: doc.get(key, ""), reverse=direction == -1)
        return matches[0] if matches else None

    def find(self, query, projection=None):
        def match(doc):
            return all(doc.get(key) == value for key, value in query.items())

        return FakeCursor([dict(doc) for doc in self.docs if match(doc)])


class FakeDB:
    def __init__(self):
        self.live_readonly_snapshots = FakeCollection()
        self.live_readonly_reports = FakeCollection()
        self.live_post_submit_reconciliation_requirements = FakeCollection()
        self.live_halts = FakeCollection()


@pytest.mark.asyncio
async def test_manual_live_pilot_readiness_blocks_when_snapshot_and_reconciliation_missing(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "disabled")
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", "true")

    status = await ManualLivePilotReadinessServiceV2(FakeDB()).checklist("user-1")

    assert status["ready"] is False
    assert status["status"] == "not_ready"
    names = {blocker["name"] for blocker in status["blockers"]}
    assert "trading_mode_live_trading" in names
    assert "global_live_gate_enabled" in names
    assert "live_readonly_snapshot_fresh" in names
    assert "live_readonly_reconciliation_fresh_and_ok" in names


@pytest.mark.asyncio
async def test_manual_live_pilot_readiness_detects_pending_reconciliation_and_halt(monkeypatch):
    now = datetime.now(UTC).isoformat()
    db = FakeDB()
    await db.live_readonly_snapshots.insert_one({"user_id": "user-1", "snapshot_timestamp": now, "created_at": now, "snapshot_hash": "snap-1", "exchange": "coinbase_exchange", "adapter_version": "test", "credential_alias": "coinbase_key_test"})
    await db.live_readonly_reports.insert_one({"user_id": "user-1", "status": "ok", "checked_at": now, "issues": [], "snapshot_hash": "snap-1"})
    await db.live_post_submit_reconciliation_requirements.insert_one({"user_id": "user-1", "status": "pending", "live_order_id": "live-1", "created_at": now})
    await db.live_halts.insert_one({"scope": "user", "user_id": "user-1", "active": True, "created_at": now})

    monkeypatch.setenv("TRADING_MODE", "live-trading")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "coinbase_exchange_v2")
    monkeypatch.setenv("LIVE_MANUAL_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("LIVE_SIGNED_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("LIVE_MAX_ORDER_NOTIONAL_USD", "5")
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", "false")

    status = await ManualLivePilotReadinessServiceV2(db).checklist("user-1")

    assert status["ready"] is False
    names = {blocker["name"] for blocker in status["blockers"]}
    assert "no_pending_post_submit_reconciliation" in names
    assert "no_active_halts" in names


@pytest.mark.asyncio
async def test_manual_live_pilot_readiness_ready_when_all_checks_pass(monkeypatch):
    now = datetime.now(UTC).isoformat()
    db = FakeDB()
    await db.live_readonly_snapshots.insert_one({"user_id": "user-1", "snapshot_timestamp": now, "created_at": now, "snapshot_hash": "snap-1", "exchange": "coinbase_exchange", "adapter_version": "test", "credential_alias": "coinbase_key_test"})
    await db.live_readonly_reports.insert_one({"user_id": "user-1", "status": "ok", "checked_at": now, "issues": [], "snapshot_hash": "snap-1"})

    monkeypatch.setenv("TRADING_MODE", "live-trading")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "coinbase_exchange_v2")
    monkeypatch.setenv("LIVE_MANUAL_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("LIVE_SIGNED_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("LIVE_MAX_ORDER_NOTIONAL_USD", "5")
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", "false")

    status = await ManualLivePilotReadinessServiceV2(db).checklist("user-1")

    assert status["ready"] is True
    assert status["status"] == "ready"
    assert status["blockers"] == []


@pytest.mark.asyncio
async def test_manual_live_pilot_readiness_blocks_stale_reconciliation(monkeypatch):
    now = datetime.now(UTC).isoformat()
    stale = (datetime.now(UTC) - timedelta(seconds=1000)).isoformat()
    db = FakeDB()
    await db.live_readonly_snapshots.insert_one({"user_id": "user-1", "snapshot_timestamp": now, "created_at": now, "snapshot_hash": "snap-1", "exchange": "coinbase_exchange", "adapter_version": "test", "credential_alias": "coinbase_key_test"})
    await db.live_readonly_reports.insert_one({"user_id": "user-1", "status": "ok", "checked_at": stale, "issues": [], "snapshot_hash": "snap-1"})

    monkeypatch.setenv("TRADING_MODE", "live-trading")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "coinbase_exchange_v2")
    monkeypatch.setenv("LIVE_MANUAL_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("LIVE_SIGNED_APPROVAL_REQUIRED", "true")
    monkeypatch.setenv("LIVE_MAX_ORDER_NOTIONAL_USD", "5")
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", "false")

    status = await ManualLivePilotReadinessServiceV2(db).checklist("user-1")

    assert status["ready"] is False
    names = {blocker["name"] for blocker in status["blockers"]}
    assert "live_readonly_reconciliation_fresh_and_ok" in names
