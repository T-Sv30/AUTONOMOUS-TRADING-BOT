import os
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-live-approval-challenges-more-than-32-chars")
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
from services.live_approval_challenge_service_v2 import LiveApprovalChallengeError, LiveApprovalChallengeServiceV2


async def cleanup(user_id: str | None = None, email: str | None = None) -> None:
    if email:
        await db.users.delete_many({"email": email})
    if user_id:
        await db.bot_configs.delete_many({"user_id": user_id})
        await db.auth_failures.delete_many({"email": email})
        await db.live_approval_challenges.delete_many({"user_id": user_id})
        await db.live_order_audits.delete_many({"user_id": user_id})
        await db.live_readonly_reports.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_approval_challenge_is_payload_bound_and_single_use():
    service = LiveApprovalChallengeServiceV2(db)
    user_id = f"user-{uuid.uuid4().hex}"
    try:
        challenge = await service.create_challenge(
            user_id=user_id,
            side="BUY",
            symbol="BTC-USD",
            notional_usd=5.0,
            expires_in_seconds=300,
        )

        assert challenge["approval_token"]
        assert challenge["intent"] == {"user_id": user_id, "side": "BUY", "symbol": "BTC-USD", "dry_run": False, "notional_usd": 5.0}

        with pytest.raises(LiveApprovalChallengeError, match="does not match"):
            await service.verify_and_consume(
                user_id=user_id,
                approval_token=challenge["approval_token"],
                side="BUY",
                symbol="ETH-USD",
                notional_usd=5.0,
            )

        verified = await service.verify_and_consume(
            user_id=user_id,
            approval_token=challenge["approval_token"],
            side="BUY",
            symbol="BTC-USD",
            notional_usd=5.0,
        )
        assert verified["status"] == "used"

        with pytest.raises(LiveApprovalChallengeError, match="already used"):
            await service.verify_and_consume(
                user_id=user_id,
                approval_token=challenge["approval_token"],
                side="BUY",
                symbol="BTC-USD",
                notional_usd=5.0,
            )
    finally:
        await cleanup(user_id=user_id)


@pytest.mark.asyncio
async def test_expired_approval_challenge_is_rejected():
    service = LiveApprovalChallengeServiceV2(db)
    user_id = f"user-{uuid.uuid4().hex}"
    try:
        challenge = await service.create_challenge(user_id=user_id, side="BUY", symbol="BTC-USD", notional_usd=5.0)
        expired_at = service.iso(service.utc_now() - timedelta(seconds=1))
        await db.live_approval_challenges.update_one(
            {"challenge_id": challenge["challenge_id"]},
            {"$set": {"expires_at": expired_at}},
        )

        with pytest.raises(LiveApprovalChallengeError, match="expired"):
            await service.verify_and_consume(
                user_id=user_id,
                approval_token=challenge["approval_token"],
                side="BUY",
                symbol="BTC-USD",
                notional_usd=5.0,
            )
    finally:
        await cleanup(user_id=user_id)


async def signup_with_execute_scope(client: httpx.AsyncClient, email: str):
    response = await client.post("/api/auth/signup", json={"email": email, "password": "LiveApprovalTest123!"})
    assert response.status_code == 200, response.text
    body = response.json()
    user_id = body["user"]["id"]
    await db.users.update_one({"id": user_id}, {"$set": {"scopes": [Scope.TRADING_LIVE_EXECUTE.value]}})
    return user_id, {"Authorization": f"Bearer {body['access_token']}"}


@pytest.mark.asyncio
async def test_non_dry_run_live_order_requires_valid_signed_challenge():
    app = create_app()
    email = f"approval-api-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_id, headers = await signup_with_execute_scope(client, email)

            blocked = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False},
            )
            assert blocked.status_code == 403
            assert "approval" in blocked.json()["error"]["message"]

            challenge = await client.post(
                "/api/live-approvals/challenge",
                headers=headers,
                json={"side": "BUY", "symbol": "BTC-USD", "notional_usd": 5.0},
            )
            assert challenge.status_code == 200, challenge.text
            approval_token = challenge.json()["approval_token"]

            # Pre-submit safety (added after this test) needs a fresh live-readonly reconciliation.
            await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})

            reached_route = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False, "approval_token": approval_token},
            )
            assert reached_route.status_code == 200, reached_route.text
            assert reached_route.json()["status"] == "blocked"

            replay = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False, "approval_token": approval_token},
            )
            assert replay.status_code == 403
            assert "already used" in replay.json()["error"]["message"]
    finally:
        await cleanup(user_id=user_id, email=email)
