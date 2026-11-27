import os

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-readiness-endpoint-tests-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

from datetime import UTC, datetime

import httpx
import pytest
from fakes import FakeDB

import app_state
from app_factory import create_app

SENSITIVE_WORDS = ["jwt", "secret", "mongo", "coinbase", "allowed_symbols", "trading_mode", "password"]


class PingFailsDB(FakeDB):
    name = "unreachable"

    async def command(self, command):
        raise RuntimeError("connection refused")


class PingOkDB(FakeDB):
    name = "fake"

    async def command(self, command):
        return {"ok": 1}


async def get_readyz(query: str = ""):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
        return await client.get(f"/readyz{query}")


def assert_non_sensitive(response):
    serialized = response.text.lower()
    for word in SENSITIVE_WORDS:
        assert word not in serialized, word


@pytest.mark.asyncio
async def test_readyz_is_ready_on_a_reachable_database_and_non_sensitive():
    response = await get_readyz()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["service"] == "Autonomous Trading Bot"
    assert_non_sensitive(response)


@pytest.mark.asyncio
async def test_readyz_returns_503_when_the_database_ping_fails(monkeypatch):
    monkeypatch.setattr(app_state, "db", PingFailsDB())

    response = await get_readyz()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "not_ready"
    assert "database" in body["failed_checks"]
    assert "connection refused" not in response.text  # no driver error details
    assert_non_sensitive(response)


@pytest.mark.asyncio
async def test_strict_readyz_returns_503_without_a_worker_heartbeat(monkeypatch):
    monkeypatch.setattr(app_state, "db", PingOkDB())

    response = await get_readyz("?strict=true")

    assert response.status_code == 503, response.text
    assert "worker_heartbeat" in response.json()["failed_checks"]
    assert_non_sensitive(response)


@pytest.mark.asyncio
async def test_strict_readyz_accepts_a_fresh_worker_heartbeat(monkeypatch):
    fake_db = PingOkDB()
    now = datetime.now(UTC).isoformat()
    await fake_db.worker_heartbeats.insert_one({"worker_id": "w1", "status": "running", "last_heartbeat_at": now, "updated_at": now, "stale_after_seconds": 30})
    monkeypatch.setattr(app_state, "db", fake_db)

    response = await get_readyz("?strict=true")

    # Strict mode also fails on environment warnings (CI uses simulated market data), so only the heartbeat check is asserted.
    body = response.json()
    if response.status_code == 200:
        assert body["status"] == "ready"
    else:
        assert "worker_heartbeat" not in body["failed_checks"], body
