"""M1.5b task 12 (P0-20): /bot-config cannot start bots or set unsafe values; list limits are capped."""

import os
import uuid

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-bot-config-validation-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

from contextlib import asynccontextmanager

import httpx
import pytest

from app_factory import create_app
from app_state import db


@asynccontextmanager
async def signed_up_client():
    email = f"bot-config-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
            response = await client.post("/api/auth/signup", json={"email": email, "password": "BotConfigPassword123!"})
            assert response.status_code == 200, response.text
            body = response.json()
            user_id = body["user"]["id"]
            yield client, user_id, {"Authorization": f"Bearer {body['access_token']}"}
    finally:
        await db.users.delete_many({"email": email})
        if user_id:
            for collection_name in ["bot_configs", "portfolio_state", "auth_failures", "alerts"]:
                await getattr(db, collection_name).delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_posting_is_active_does_not_start_or_stop_the_bot():
    async with signed_up_client() as (client, user_id, headers):
        response = await client.post("/api/bot-config", headers=headers, json={"is_active": True})
        assert response.status_code == 200, response.text
        assert (await db.bot_configs.find_one({"user_id": user_id}))["is_active"] is False

        started = await client.post("/api/bot/start", headers=headers)
        assert started.status_code == 200, started.text
        response = await client.post("/api/bot-config", headers=headers, json={"is_active": False, "symbols": ["BTC-USD"]})
        assert response.status_code == 200, response.text
        assert (await db.bot_configs.find_one({"user_id": user_id}))["is_active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"symbols": ["DOGE-USD"]},
        {"symbols": []},
        {"symbols": ["BTC-USD", "BTC-USD"]},
        {"max_daily_loss": 5},
        {"max_daily_loss": 0},
        {"max_drawdown_pct": 0.5},
        {"max_position_notional": 1},
        {"max_position_notional": 1_000_000},
        {"cooldown_seconds": -1},
        {"cooldown_seconds": 86401},
    ],
)
async def test_unsafe_bot_config_values_are_rejected(body):
    async with signed_up_client() as (client, user_id, headers):
        before = await db.bot_configs.find_one({"user_id": user_id}, {"_id": 0})
        response = await client.post("/api/bot-config", headers=headers, json=body)
        assert response.status_code == 422, response.text
        assert await db.bot_configs.find_one({"user_id": user_id}, {"_id": 0}) == before


@pytest.mark.asyncio
async def test_partial_update_leaves_other_fields_unchanged():
    async with signed_up_client() as (client, user_id, headers):
        await db.bot_configs.update_one({"user_id": user_id}, {"$set": {"max_daily_loss": 0.02, "cooldown_seconds": 600, "risk_target_vol": 0.2}})

        response = await client.post("/api/bot-config", headers=headers, json={"symbols": ["ETH-USD"], "capital_floor": 0.9})

        assert response.status_code == 200, response.text
        stored = await db.bot_configs.find_one({"user_id": user_id}, {"_id": 0})
        assert stored["symbols"] == ["ETH-USD"]
        assert stored["max_daily_loss"] == 0.02
        assert stored["cooldown_seconds"] == 600
        assert stored["risk_target_vol"] == 0.2
        assert "capital_floor" not in stored  # accepted for old clients, never stored


@pytest.mark.asyncio
async def test_in_range_values_are_stored():
    async with signed_up_client() as (client, user_id, headers):
        body = {"max_daily_loss": 0.1, "max_drawdown_pct": 0.25, "max_position_notional": 5, "cooldown_seconds": 0}
        response = await client.post("/api/bot-config", headers=headers, json=body)
        assert response.status_code == 200, response.text
        stored = await db.bot_configs.find_one({"user_id": user_id}, {"_id": 0})
        assert {key: stored[key] for key in body} == body


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/alerts", "/api/ledger/entries", "/api/live-trading/audits", "/api/ops/workers"])
async def test_list_limits_are_capped(path):
    async with signed_up_client() as (client, _user_id, headers):
        too_big = await client.get(f"{path}?limit=100000", headers=headers)
        zero = await client.get(f"{path}?limit=0", headers=headers)
        ok = await client.get(f"{path}?limit=500", headers=headers)

    assert too_big.status_code == 422, too_big.text
    assert zero.status_code == 422, zero.text
    assert ok.status_code == 200, ok.text
