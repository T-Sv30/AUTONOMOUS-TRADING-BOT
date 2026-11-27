import pytest

from services.manual_live_pilot_workflow_service_v2 import ManualLivePilotWorkflowServiceV2


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        # Motor accepts sort("field", direction) and sort([("field", direction), ...]).
        keys = [(key, direction)] if isinstance(key, str) else list(key)
        for field, field_direction in reversed(keys):
            self.docs = sorted(self.docs, key=lambda doc, f=field: doc.get(f, ""), reverse=field_direction == -1)
        return self

    def skip(self, count):
        self.docs = self.docs[count:]
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

    async def update_one(self, query, update, upsert=False):
        target = None
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                target = doc
                break
        if target is None:
            if not upsert:
                return
            target = dict(query)
            for key, value in update.get("$setOnInsert", {}).items():
                target[key] = value
            self.docs.append(target)
        for key, value in update.get("$set", {}).items():
            target[key] = value

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
        self.live_post_submit_reconciliation_requirements = FakeCollection()
        self.live_readonly_snapshots = FakeCollection()
        self.live_readonly_reports = FakeCollection()
        self.live_order_transitions = FakeCollection()
        self.live_order_audits = FakeCollection()
        self.manual_live_pilot_reports = FakeCollection()


@pytest.mark.asyncio
async def test_pending_reconciliation_requirements_lists_pending_only():
    db = FakeDB()
    await db.live_post_submit_reconciliation_requirements.insert_one({"user_id": "user-1", "live_order_id": "live-1", "status": "pending", "created_at": "2026-01-01T00:00:00+00:00"})
    await db.live_post_submit_reconciliation_requirements.insert_one({"user_id": "user-1", "live_order_id": "live-2", "status": "resolved", "created_at": "2026-01-01T00:01:00+00:00"})

    result = await ManualLivePilotWorkflowServiceV2(db).pending_reconciliation_requirements("user-1")

    assert result["pending_count"] == 1
    assert result["pending"][0]["live_order_id"] == "live-1"


@pytest.mark.asyncio
async def test_resolve_reconciliation_requirement_updates_pending_record():
    db = FakeDB()
    await db.live_post_submit_reconciliation_requirements.insert_one({"user_id": "user-1", "live_order_id": "live-1", "status": "pending", "created_at": "2026-01-01T00:00:00+00:00"})
    await db.live_readonly_reports.insert_one({"user_id": "user-1", "status": "ok", "checked_at": "2026-01-01T00:01:00+00:00", "issues": [], "snapshot_hash": "snap-1"})
    await db.live_readonly_snapshots.insert_one({"user_id": "user-1", "snapshot_timestamp": "2026-01-01T00:01:00+00:00", "created_at": "2026-01-01T00:01:00+00:00", "snapshot_hash": "snap-1"})

    result = await ManualLivePilotWorkflowServiceV2(db).resolve_reconciliation_requirement(user_id="user-1", live_order_id="live-1", resolution="verified", notes="matched exchange")
    saved = await db.live_post_submit_reconciliation_requirements.find_one({"user_id": "user-1", "live_order_id": "live-1"})

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert saved["status"] == "resolved"
    assert saved["resolution"] == "verified"
    assert saved["notes"] == "matched exchange"
    assert saved["latest_reconciliation_report"]["status"] == "ok"


@pytest.mark.asyncio
async def test_resolve_reconciliation_requirement_reports_missing_record():
    result = await ManualLivePilotWorkflowServiceV2(FakeDB()).resolve_reconciliation_requirement(user_id="user-1", live_order_id="missing", resolution="verified")

    assert result["success"] is False
    assert result["status"] == "missing"


@pytest.mark.asyncio
async def test_build_pilot_report_hashes_and_persists_report():
    db = FakeDB()
    await db.live_order_transitions.insert_one({"user_id": "user-1", "order_id": "live-1", "sequence": 1, "state": "requested"})
    await db.live_order_transitions.insert_one({"user_id": "user-1", "order_id": "live-1", "sequence": 2, "state": "reconciliation_pending"})
    await db.live_post_submit_reconciliation_requirements.insert_one({"user_id": "user-1", "live_order_id": "live-1", "status": "resolved", "created_at": "2026-01-01T00:00:00+00:00"})
    await db.live_order_audits.insert_one({"user_id": "user-1", "live_order_id": "live-1", "created_at": "2026-01-01T00:00:00+00:00", "audit_hash": "audit-1"})
    await db.live_readonly_snapshots.insert_one({"user_id": "user-1", "snapshot_timestamp": "2026-01-01T00:01:00+00:00", "created_at": "2026-01-01T00:01:00+00:00", "snapshot_hash": "snap-1"})
    await db.live_readonly_reports.insert_one({"user_id": "user-1", "status": "ok", "checked_at": "2026-01-01T00:01:00+00:00", "issues": [], "snapshot_hash": "snap-1"})

    report = await ManualLivePilotWorkflowServiceV2(db).build_pilot_report("user-1", "live-1")

    assert report["status"] == "complete"
    assert report["transition_count"] == 2
    assert report["audit_count"] == 1
    assert report["report_hash"]
    assert len(db.manual_live_pilot_reports.docs) == 1
    assert db.manual_live_pilot_reports.docs[0]["live_order_id"] == "live-1"


@pytest.mark.asyncio
async def test_list_pilot_reports_returns_user_reports():
    db = FakeDB()
    await db.manual_live_pilot_reports.insert_one({"user_id": "user-1", "live_order_id": "live-1", "generated_at": "2026-01-01T00:00:00+00:00"})
    await db.manual_live_pilot_reports.insert_one({"user_id": "user-2", "live_order_id": "live-2", "generated_at": "2026-01-01T00:00:00+00:00"})

    result = await ManualLivePilotWorkflowServiceV2(db).list_pilot_reports("user-1")

    assert result["count"] == 1
    assert result["reports"][0]["live_order_id"] == "live-1"
