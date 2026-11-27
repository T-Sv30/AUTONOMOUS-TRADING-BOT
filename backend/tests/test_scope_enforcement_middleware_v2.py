import os
import uuid
from datetime import UTC, datetime

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-scope-enforcement-tests-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

import httpx
import pytest

from app_factory import create_app
from app_state import db
from services.authorization_v2 import Scope


async def signup_user(client: httpx.AsyncClient, email: str):
    response = await client.post("/api/auth/signup", json={"email": email, "password": "ScopeTestPassword123!"})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


async def cleanup_user(user_id: str | None, email: str) -> None:
    await db.users.delete_many({"email": email})
    if user_id:
        for collection_name in [
            "bot_configs",
            "auth_failures",
            "alerts",
            "live_readonly_reports",
            "live_order_transitions",
            "live_order_audits",
            "live_risk_decisions",
            "live_approval_challenges",
            "live_halts",
        ]:
            await getattr(db, collection_name).delete_many({"user_id": user_id})


async def seed_fresh_reconciliation(user_id: str) -> None:
    # Pre-submit safety needs a fresh live-readonly reconciliation before any non-dry-run order.
    await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})


@pytest.mark.asyncio
async def test_default_user_can_view_live_gate_with_preview_scope():
    app = create_app()
    email = f"scope-preview-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            response = await client.get("/api/live-trading/gate", headers=headers)

        assert response.status_code == 200, response.text
        assert "required_adapter" in response.json()
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_default_user_cannot_non_dry_run_live_order_without_execute_scope():
    app = create_app()
    email = f"scope-block-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            response = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False},
            )

        assert response.status_code == 403
        payload = response.json()
        assert payload["error"]["code"] == "FORBIDDEN"
        assert Scope.TRADING_LIVE_EXECUTE.value in payload["error"]["message"]
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_explicit_live_execute_scope_reaches_live_gate_for_non_dry_run_order():
    app = create_app()
    email = f"scope-execute-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            await db.users.update_one({"id": user_id}, {"$set": {"scopes": [Scope.TRADING_LIVE_EXECUTE.value]}})
            await seed_fresh_reconciliation(user_id)
            response = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False},
            )

        # The scope check passes; the live trading service then rejects the order
        # because it carries no signed approval challenge.
        body = response.json()
        assert "Missing required scope" not in str(body)
        assert response.status_code == 200, response.text
        assert body["status"] == "approval_rejected"
        assert "approval" in body["reason"].lower()
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_default_user_cannot_manage_indexes_without_ops_scope():
    app = create_app()
    email = f"scope-index-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            response = await client.post("/api/ops/indexes/ensure", headers=headers)

        assert response.status_code == 403
        assert Scope.OPS_INDEXES.value in response.json()["error"]["message"]
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_explicit_ops_indexes_scope_allows_index_management():
    app = create_app()
    email = f"scope-index-allow-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            await db.users.update_one({"id": user_id}, {"$set": {"scopes": [Scope.OPS_INDEXES.value]}})
            response = await client.post("/api/ops/indexes/ensure", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,order",
    [
        ("/api/live-trading/market-buy", {"symbol": "BTC-USD", "notional_usd": 5.0}),
        ("/api/live-trading/market-sell", {"symbol": "BTC-USD", "base_units": 0.0001, "reference_price": 50000.0}),
    ],
)
@pytest.mark.parametrize("value", [0, "false", "no", "off", None])
async def test_live_order_rejects_non_boolean_dry_run(path, order, value):
    app = create_app()
    email = f"scope-strict-dry-run-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            await db.users.update_one({"id": user_id}, {"$set": {"scopes": [Scope.TRADING_LIVE_EXECUTE.value]}})
            await seed_fresh_reconciliation(user_id)
            response = await client.post(path, headers=headers, json={**order, "dry_run": value})

        assert response.status_code == 422, f"dry_run={value!r} answered {response.status_code}: {response.text}"
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, "false"])
async def test_non_boolean_dry_run_needs_live_execute_scope(value):
    app = create_app()
    email = f"scope-strict-dry-run-scope-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            response = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": value},
            )

        assert response.status_code == 403, response.text
        assert Scope.TRADING_LIVE_EXECUTE.value in response.json()["error"]["message"]
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_live_order_without_dry_run_key_is_a_dry_run_preview():
    app = create_app()
    email = f"scope-default-dry-run-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            response = await client.post("/api/live-trading/market-buy", headers=headers, json={"symbol": "BTC-USD", "notional_usd": 5.0})

        assert response.status_code == 200, response.text
        assert response.json()["gate"]["dry_run"] is True
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_emergency_halt_release_requires_ops_halt_scope():
    app = create_app()
    email = f"scope-halt-release-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            # A role with no scopes at all, so ops:halt is missing.
            await db.users.update_one({"id": user_id}, {"$set": {"roles": ["no-scopes"], "scopes": []}})
            response = await client.post("/api/ops/emergency-halt/release", headers=headers, json={"scope": "user"})

        assert response.status_code == 403, response.text
        assert Scope.OPS_HALT.value in response.json()["error"]["message"]
    finally:
        await cleanup_user(user_id, email)


@pytest.mark.asyncio
async def test_resolve_unknown_live_order_requires_live_execute_scope():
    app = create_app()
    email = f"scope-resolve-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_user(client, email)
            response = await client.post("/api/live-trading/orders/live_order_missing/resolve", headers=headers)

        assert response.status_code == 403, response.text
        assert Scope.TRADING_LIVE_EXECUTE.value in response.json()["error"]["message"]
    finally:
        await cleanup_user(user_id, email)
