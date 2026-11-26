import os
import uuid
from datetime import UTC, datetime

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-live-state-tests-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("LIVE_REQUIRE_FRESH_RECONCILIATION", "True")
os.environ.setdefault("LIVE_MAX_RECONCILIATION_AGE_SECONDS", "300")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

import httpx
import pytest

import api_routes_v3
from app_factory import create_app
from app_state import db
from services.live_order_state_service_v2 import LiveOrderState, LiveOrderStateError, LiveOrderStateServiceV2
from services.live_pre_submit_safety_service_v2 import LivePreSubmitSafetyServiceV2


async def cleanup(user_id: str):
    await db.live_order_transitions.delete_many({"user_id": user_id})
    await db.live_readonly_reports.delete_many({"user_id": user_id})
    await db.live_halts.delete_many({"user_id": user_id})
    await db.live_halts.delete_many({"scope": "global"})


@pytest.mark.asyncio
async def test_live_order_state_machine_hash_chain_and_invalid_transition_detection():
    user_id = "state-user-1"
    service = LiveOrderStateServiceV2(db)
    try:
        created = await service.create_order(user_id=user_id, symbol="BTC-USD", side="BUY", notional_usd=5, request_id="req-1")
        assert created["state"] == LiveOrderState.REQUESTED.value
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.GATE_CHECKED.value, reason="gate ok")
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.RISK_CHECKED.value, reason="risk ok")
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.APPROVAL_REQUIRED.value, reason="approval required")
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.APPROVED.value, reason="approved")

        verified = await service.verify_chain(created["order_id"])
        assert verified["status"] == "ok"
        assert verified["checked_records"] == 5

        with pytest.raises(LiveOrderStateError):
            await service.transition(order_id=created["order_id"], next_state=LiveOrderState.RECONCILED.value, reason="invalid skip")
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_blocks_without_fresh_reconciliation():
    user_id = "safety-user-no-recon"
    try:
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
        assert result["allowed"] is False
        assert "fresh live-readonly reconciliation" in result["reason"]
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_allows_with_fresh_reconciliation_and_no_unresolved_order():
    user_id = "safety-user-fresh"
    try:
        await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
        assert result["allowed"] is True
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_blocks_unresolved_live_order():
    user_id = "safety-user-unresolved"
    states = LiveOrderStateServiceV2(db)
    try:
        await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
        await states.create_order(user_id=user_id, symbol="BTC-USD", side="BUY", notional_usd=5)
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
        assert result["allowed"] is False
        assert "unresolved live order" in result["reason"]
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_blocks_active_halt():
    user_id = "safety-user-halt"
    safety = LivePreSubmitSafetyServiceV2(db)
    try:
        await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
        await safety.create_halt(user_id=user_id, scope="user", reason="test halt", created_by="pytest")
        result = await safety.safe_check(user_id)
        assert result["allowed"] is False
        assert "halt" in result["reason"]
    finally:
        await cleanup(user_id)


async def signup(client: httpx.AsyncClient, email: str):
    response = await client.post("/api/auth/signup", json={"email": email, "password": "HaltTestPassword123!"})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


async def cleanup_route_user(user_id: str | None, email: str):
    await db.users.delete_many({"email": email})
    if user_id:
        await cleanup(user_id)
        for collection_name in ["bot_configs", "alerts", "auth_failures"]:
            await getattr(db, collection_name).delete_many({"user_id": user_id})


async def fresh_reconciliation(user_id: str):
    await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})


@pytest.mark.asyncio
async def test_emergency_halt_blocks_live_orders_until_released():
    email = f"halt-user-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
            user_id, headers = await signup(client, email)
            await fresh_reconciliation(user_id)
            assert (await LivePreSubmitSafetyServiceV2(db).safe_check(user_id))["allowed"] is True

            halted = await client.post("/api/ops/emergency-halt", headers=headers, json={"reason": "pytest emergency"})
            assert halted.status_code == 200, halted.text

            # A fresh service instance stands in for a restart: the halt lives in the database.
            blocked = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
            assert blocked["allowed"] is False, blocked
            assert "halt" in blocked["reason"]
            halt = await db.live_halts.find_one({"user_id": user_id, "active": True}, {"_id": 0})
            assert halt["scope"] == "user"
            assert halt["created_by"] == user_id
            assert halt["reason"] == "pytest emergency"

            released = await client.post("/api/ops/emergency-halt/release", headers=headers, json={"scope": "user"})
            assert released.status_code == 200, released.text
            assert released.json()["released_count"] == 1

        assert (await LivePreSubmitSafetyServiceV2(db).safe_check(user_id))["allowed"] is True
        record = await db.live_halts.find_one({"user_id": user_id}, {"_id": 0})
        assert record["active"] is False
        assert record["released_by"] == user_id
        assert record["released_at"]
        assert await db.alerts.find_one({"user_id": user_id, "type": "operator_emergency_halt_released"})
    finally:
        await cleanup_route_user(user_id, email)


@pytest.mark.asyncio
async def test_global_emergency_halt_blocks_a_different_user():
    admin_email = f"halt-admin-{uuid.uuid4().hex}@example.com"
    other_email = f"halt-other-{uuid.uuid4().hex}@example.com"
    admin_id = other_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
            admin_id, admin_headers = await signup(client, admin_email)
            other_id, _ = await signup(client, other_email)
            await fresh_reconciliation(other_id)

            halted = await client.post("/api/ops/emergency-halt", headers=admin_headers, json={"reason": "pytest global", "scope": "global"})
            assert halted.status_code == 200, halted.text

            blocked = await LivePreSubmitSafetyServiceV2(db).safe_check(other_id)
            assert blocked["allowed"] is False, blocked
            assert "halt" in blocked["reason"]

            released = await client.post("/api/ops/emergency-halt/release", headers=admin_headers, json={"scope": "global"})
            assert released.status_code == 200, released.text
            assert released.json()["released_count"] == 1

        assert (await LivePreSubmitSafetyServiceV2(db).safe_check(other_id))["allowed"] is True
    finally:
        await cleanup_route_user(admin_id, admin_email)
        await cleanup_route_user(other_id, other_email)


@pytest.mark.asyncio
async def test_only_ops_admins_create_global_halts_or_release_halts(monkeypatch):
    monkeypatch.setattr(api_routes_v3, "OPS_ADMIN_ENABLED", True)
    monkeypatch.setattr(api_routes_v3, "OPS_ADMIN_EMAILS", set())
    email = f"halt-non-admin-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
            user_id, headers = await signup(client, email)

            global_halt = await client.post("/api/ops/emergency-halt", headers=headers, json={"reason": "not allowed", "scope": "global"})
            assert global_halt.status_code == 403, global_halt.text
            assert await db.live_halts.count_documents({"scope": "global", "active": True}) == 0

            own_halt = await client.post("/api/ops/emergency-halt", headers=headers, json={"reason": "self halt"})
            assert own_halt.status_code == 200, own_halt.text

            release = await client.post("/api/ops/emergency-halt/release", headers=headers, json={"scope": "user"})
            assert release.status_code == 403, release.text

        assert await db.live_halts.count_documents({"user_id": user_id, "active": True}) == 1
    finally:
        await cleanup_route_user(user_id, email)
