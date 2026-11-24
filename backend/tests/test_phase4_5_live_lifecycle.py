import pytest

from services.live_manual_order_lifecycle_service_v2 import LiveManualOrderLifecycleServiceV2
from services.live_order_state_service_v2 import LiveOrderState


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda doc: doc.get(key, 0), reverse=direction == -1)
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
            for key, value in query.items():
                if isinstance(value, dict) and "$in" in value:
                    if doc.get(key) not in value["$in"]:
                        return False
                elif doc.get(key) != value:
                    return False
            return True

        matches = [dict(doc) for doc in self.docs if match(doc)]
        if sort:
            for key, direction in reversed(sort):
                matches = sorted(matches, key=lambda doc: doc.get(key, 0), reverse=direction == -1)
        return matches[0] if matches else None

    async def update_many(self, query, update):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value

    def find(self, query, projection=None):
        return FakeCursor([dict(doc) for doc in self.docs if all(doc.get(k) == v for k, v in query.items())])


class FakeDB:
    def __init__(self):
        self.live_order_transitions = FakeCollection()
        self.live_risk_decisions = FakeCollection()
        self.live_halts = FakeCollection()
        self.live_readonly_reports = FakeCollection()
        self.live_post_submit_reconciliation_requirements = FakeCollection()


@pytest.mark.asyncio
async def test_lifecycle_dry_run_completes_to_reconciled_without_reconciliation_requirement():
    db = FakeDB()
    lifecycle = LiveManualOrderLifecycleServiceV2(db)
    order = await lifecycle.begin(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, base_units=None, client_order_id="client-1")
    live_order_id = order["order_id"]

    await lifecycle.gate_checked(live_order_id, {"allowed": True})
    risk = await lifecycle.risk_checked(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, user_config={"live_allowed_symbols": ["BTC-USD"], "live_max_order_notional_usd": 10.0}, live_order_id=live_order_id, dry_run=True)
    await lifecycle.approval_recorded(live_order_id=live_order_id, dry_run=True, approval_token=None)
    await lifecycle.submitted(live_order_id, {"status": "dry_run", "client_order_id": "client-1"}, True)
    requirement = await lifecycle.finalized_from_order(user_id="user-1", live_order_id=live_order_id, order={"status": "dry_run", "client_order_id": "client-1"}, dry_run=True)

    latest = await lifecycle.states.latest_transition(live_order_id)
    chain = await lifecycle.states.verify_chain(live_order_id)

    assert risk["decision"] == "allow"
    assert latest["state"] == LiveOrderState.RECONCILED.value
    assert requirement["required"] is False
    assert db.live_post_submit_reconciliation_requirements.docs == []
    assert chain["status"] == "ok"


@pytest.mark.asyncio
async def test_lifecycle_non_dry_run_filled_order_requires_reconciliation():
    db = FakeDB()
    lifecycle = LiveManualOrderLifecycleServiceV2(db)
    order = await lifecycle.begin(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, base_units=None, client_order_id="client-1")
    live_order_id = order["order_id"]

    await lifecycle.gate_checked(live_order_id, {"allowed": True})
    await lifecycle.risk_checked(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, user_config={"live_allowed_symbols": ["BTC-USD"], "live_max_order_notional_usd": 10.0}, live_order_id=live_order_id, dry_run=False)
    await lifecycle.approval_recorded(live_order_id=live_order_id, dry_run=False, approval_token="token")
    await lifecycle.submitted(live_order_id, {"status": "filled", "order_id": "ex-1", "client_order_id": "client-1"}, False)
    requirement = await lifecycle.finalized_from_order(user_id="user-1", live_order_id=live_order_id, order={"status": "filled", "order_id": "ex-1", "client_order_id": "client-1"}, dry_run=False)

    latest = await lifecycle.states.latest_transition(live_order_id)

    assert latest["state"] == LiveOrderState.RECONCILIATION_PENDING.value
    assert requirement["required"] is True
    assert requirement["status"] == "pending"
    assert requirement["exchange_order_id"] == "ex-1"
    assert len(db.live_post_submit_reconciliation_requirements.docs) == 1


@pytest.mark.asyncio
async def test_lifecycle_risk_blocks_symbol_outside_allowlist():
    db = FakeDB()
    lifecycle = LiveManualOrderLifecycleServiceV2(db)
    order = await lifecycle.begin(user_id="user-1", symbol="DOGE-USD", side="BUY", notional_usd=5.0, base_units=None, client_order_id="client-1")
    live_order_id = order["order_id"]

    await lifecycle.gate_checked(live_order_id, {"allowed": True})
    decision = await lifecycle.risk_checked(user_id="user-1", symbol="DOGE-USD", side="BUY", notional_usd=5.0, user_config={"live_allowed_symbols": ["BTC-USD"], "live_max_order_notional_usd": 10.0}, live_order_id=live_order_id, dry_run=False)
    await lifecycle.blocked(live_order_id, "manual live order blocked by risk", {"risk_decision": decision})

    latest = await lifecycle.states.latest_transition(live_order_id)

    assert decision["decision"] == "block"
    assert latest["state"] == LiveOrderState.FAILED.value


@pytest.mark.asyncio
async def test_lifecycle_pre_submit_block_halts_order_when_active_halt_exists():
    db = FakeDB()
    lifecycle = LiveManualOrderLifecycleServiceV2(db)
    await db.live_halts.insert_one({"scope": "user", "user_id": "user-1", "active": True, "created_at": "2026-01-01T00:00:00+00:00"})
    order = await lifecycle.begin(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, base_units=None, client_order_id="client-1")
    live_order_id = order["order_id"]
    await lifecycle.gate_checked(live_order_id, {"allowed": True})
    await lifecycle.risk_checked(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, user_config={"live_allowed_symbols": ["BTC-USD"], "live_max_order_notional_usd": 10.0}, live_order_id=live_order_id, dry_run=False)
    await lifecycle.approval_recorded(live_order_id=live_order_id, dry_run=False, approval_token="token")

    result = await lifecycle.pre_submit_checked(user_id="user-1", live_order_id=live_order_id, dry_run=False)
    latest = await lifecycle.states.latest_transition(live_order_id)

    assert result["allowed"] is False
    assert latest["state"] == LiveOrderState.HALTED.value


@pytest.mark.asyncio
async def test_hash_chain_survives_is_latest_flips_but_detects_edited_history():
    db = FakeDB()
    lifecycle = LiveManualOrderLifecycleServiceV2(db)
    order = await lifecycle.begin(user_id="user-1", symbol="BTC-USD", side="BUY", notional_usd=5.0, base_units=None, client_order_id="client-chain")
    live_order_id = order["order_id"]
    await lifecycle.gate_checked(live_order_id, {"allowed": True})

    assert (await lifecycle.states.verify_chain(live_order_id))["status"] == "ok"

    first = next(doc for doc in db.live_order_transitions.docs if doc["order_id"] == live_order_id and doc["sequence"] == 1)
    first["reason"] = "edited after the fact"

    tampered = await lifecycle.states.verify_chain(live_order_id)
    assert tampered["status"] == "tamper_detected"
    assert {"index": 0, "type": "payload_hash_mismatch"} in tampered["issues"]
