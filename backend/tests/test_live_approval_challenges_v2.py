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
from fakes import FakeDB

from app_factory import create_app
from app_state import db
from services.authorization_v2 import Scope
from services.live_approval_challenge_service_v2 import LiveApprovalChallengeError, LiveApprovalChallengeServiceV2
from services.live_trading_gate_v2 import LiveTradingGateConfig, LiveTradingGateV2
from services.live_trading_service_v2 import LiveTradingServiceV2


async def cleanup(user_id: str | None = None, email: str | None = None) -> None:
    if email:
        await db.users.delete_many({"email": email})
    if user_id:
        await db.bot_configs.delete_many({"user_id": user_id})
        await db.auth_failures.delete_many({"email": email})
        await db.live_approval_challenges.delete_many({"user_id": user_id})
        await db.live_order_audits.delete_many({"user_id": user_id})
        await db.live_readonly_reports.delete_many({"user_id": user_id})
        await db.live_order_transitions.delete_many({"user_id": user_id})
        await db.live_risk_decisions.delete_many({"user_id": user_id})


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


@pytest.mark.asyncio
async def test_challenge_stores_a_datetime_expiry_for_the_ttl_index():
    # M1.5b task 7 (P0-10): Mongo TTL indexes only expire BSON dates, never ISO strings.
    fake_db = FakeDB()
    challenge = await LiveApprovalChallengeServiceV2(fake_db).create_challenge(user_id="user-ttl", side="BUY", symbol="BTC-USD", notional_usd=5.0)

    stored = fake_db.live_approval_challenges.docs[0]
    assert isinstance(stored.get("expires_at_ts"), datetime)
    assert stored["expires_at_ts"].tzinfo is not None
    assert stored["expires_at_ts"] == datetime.fromisoformat(challenge["expires_at"])
    assert stored["expires_at"] == challenge["expires_at"]  # existing readers keep the ISO field


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
            # Pre-submit safety needs a fresh live-readonly reconciliation.
            await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})

            # The live trading service, not the middleware, verifies and consumes the challenge.
            blocked = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False},
            )
            assert blocked.status_code == 200, blocked.text
            assert blocked.json()["status"] == "approval_rejected"
            assert "approval" in blocked.json()["reason"]

            challenge = await client.post(
                "/api/live-approvals/challenge",
                headers=headers,
                json={"side": "BUY", "symbol": "BTC-USD", "notional_usd": 5.0},
            )
            assert challenge.status_code == 200, challenge.text
            approval_token = challenge.json()["approval_token"]

            reached_gate = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False, "approval_token": approval_token},
            )
            assert reached_gate.status_code == 200, reached_gate.text
            assert reached_gate.json()["status"] == "blocked"

            replay = await client.post(
                "/api/live-trading/market-buy",
                headers=headers,
                json={"symbol": "BTC-USD", "notional_usd": 5.0, "dry_run": False, "approval_token": approval_token},
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["status"] == "approval_rejected"
            assert "already used" in replay.json()["reason"]
    finally:
        await cleanup(user_id=user_id, email=email)


class RecordingLiveAdapter:
    def __init__(self):
        self.calls = []

    async def place_market_buy(self, symbol, notional_usd, client_order_id=None, dry_run=False):
        self.calls.append(("BUY", symbol, notional_usd, dry_run))
        return {"success": True, "status": "dry_run" if dry_run else "submitted", "symbol": symbol, "side": "BUY", "client_order_id": client_order_id}

    async def place_market_sell(self, symbol, base_units, client_order_id=None, dry_run=False):
        self.calls.append(("SELL", symbol, base_units, dry_run))
        return {"success": True, "status": "dry_run" if dry_run else "submitted", "symbol": symbol, "side": "SELL", "client_order_id": client_order_id}


def open_live_service(monkeypatch, fake_db, adapter):
    """A live trading service whose gate and pre-submit checks all pass, so only the challenge decides."""
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    fake_db.bot_configs.docs.append({"user_id": "u1", "live_trading_enabled": True})
    fake_db.live_readonly_reports.docs.append({"user_id": "u1", "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
    gate = LiveTradingGateV2(LiveTradingGateConfig(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2", signed_approval_required=True))
    return LiveTradingServiceV2(fake_db, adapter=adapter, gate=gate)


@pytest.mark.asyncio
async def test_service_rejects_non_dry_run_without_valid_challenge(monkeypatch):
    fake_db = FakeDB()
    adapter = RecordingLiveAdapter()
    service = open_live_service(monkeypatch, fake_db, adapter)

    result = await service.place_market_buy("u1", "BTC-USD", 10.0, approval_token="not-a-challenge", dry_run=False)

    assert result["status"] == "approval_rejected", result
    assert result["success"] is False
    assert adapter.calls == []
    assert [audit["status"] for audit in fake_db.live_order_audits.docs] == ["approval_rejected"]


@pytest.mark.asyncio
async def test_service_rejects_challenge_for_a_different_order(monkeypatch):
    fake_db = FakeDB()
    adapter = RecordingLiveAdapter()
    service = open_live_service(monkeypatch, fake_db, adapter)
    challenge = await LiveApprovalChallengeServiceV2(fake_db).create_challenge(user_id="u1", side="BUY", symbol="BTC-USD", notional_usd=5.0)

    result = await service.place_market_buy("u1", "BTC-USD", 10.0, approval_token=challenge["approval_token"], dry_run=False)

    assert result["status"] == "approval_rejected", result
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_service_consumes_a_valid_challenge_once(monkeypatch):
    fake_db = FakeDB()
    adapter = RecordingLiveAdapter()
    service = open_live_service(monkeypatch, fake_db, adapter)
    challenge = await LiveApprovalChallengeServiceV2(fake_db).create_challenge(
        user_id="u1", side="SELL", symbol="BTC-USD", base_units=0.0001, reference_price=50000.0
    )

    first = await service.place_market_sell("u1", "BTC-USD", 0.0001, reference_price=50000.0, approval_token=challenge["approval_token"], dry_run=False)

    assert first["status"] == "submitted", first
    assert adapter.calls == [("SELL", "BTC-USD", 0.0001, False)]
    stored = await fake_db.live_approval_challenges.find_one({"challenge_id": challenge["challenge_id"]})
    assert stored["status"] == "used"
