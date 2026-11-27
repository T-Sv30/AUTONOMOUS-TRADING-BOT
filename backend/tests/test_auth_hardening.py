"""M1.5b task 13 (P0-21): lockout cannot be weaponized, signup hides accounts, tokens are short and revocable."""

import os
import uuid

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-auth-hardening-tests-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

import random
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from pydantic import ValidationError

from app_factory import create_app
from app_state import db
from services.settings_v2 import SettingsV2

PASSWORD = "AuthHardeningPassword123!"


def random_ip() -> str:
    # A fresh IP per test keeps per-IP counters from leaking between tests in the shared CI database.
    return f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


@asynccontextmanager
async def client_from(ip: str):
    transport = httpx.ASGITransport(app=create_app(), client=(ip, 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def signup(email: str, ip: str):
    async with client_from(ip) as client:
        response = await client.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


async def cleanup(emails: list[str], user_id: str | None = None) -> None:
    for email in emails:
        await db.users.delete_many({"email": email})
        await db.auth_failures.delete_many({"email": email})
    if user_id:
        for collection_name in ["bot_configs", "portfolio_state", "alerts"]:
            await getattr(db, collection_name).delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_attacker_ip_cannot_lock_the_owner_out_from_another_ip():
    email = f"victim-{uuid.uuid4().hex}@example.com"
    attacker_ip, owner_ip = random_ip(), random_ip()
    user_id = None
    try:
        user_id = (await signup(email, owner_ip))["user"]["id"]
        async with client_from(attacker_ip) as attacker:
            for _ in range(8):
                bad = await attacker.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
                assert bad.status_code == 401, bad.text

            async with client_from(owner_ip) as owner:
                good = await owner.post("/api/auth/login", json={"email": email, "password": PASSWORD})
            assert good.status_code == 200, good.text

            # The owner's success does not reset the attacker's counter.
            still_locked = await attacker.post("/api/auth/login", json={"email": email, "password": PASSWORD})
            assert still_locked.status_code == 429, still_locked.text
    finally:
        await cleanup([email], user_id)


@pytest.mark.asyncio
async def test_one_ip_spraying_many_emails_is_throttled():
    spray_ip = random_ip()
    emails = [f"spray-{n}-{uuid.uuid4().hex}@example.com" for n in range(31)]
    try:
        async with client_from(spray_ip) as client:
            for email in emails[:30]:
                bad = await client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
                assert bad.status_code == 401, bad.text
            throttled = await client.post("/api/auth/login", json={"email": emails[30], "password": "wrong-password"})
        assert throttled.status_code == 429, throttled.text

        async with client_from(random_ip()) as other:
            unaffected = await other.post("/api/auth/login", json={"email": emails[30], "password": "wrong-password"})
        assert unaffected.status_code == 401, unaffected.text
    finally:
        await cleanup(emails)


@pytest.mark.asyncio
async def test_failed_logins_record_email_ip_and_a_datetime():
    # M1.5b task 7 (P0-10) and task 13: the TTL index needs a BSON date; the throttle needs the IP.
    email = f"failure-record-{uuid.uuid4().hex}@example.com"
    ip = random_ip()
    try:
        async with client_from(ip) as client:
            await client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        record = await db.auth_failures.find_one({"email": email}, {"_id": 0})
        assert isinstance(record.get("created_at_ts"), datetime)
        assert record.get("ip") == ip
    finally:
        await cleanup([email])


@pytest.mark.asyncio
async def test_signup_with_an_existing_email_gives_the_generic_error():
    email = f"existing-{uuid.uuid4().hex}@example.com"
    ip = random_ip()
    user_id = None
    try:
        user_id = (await signup(email, ip))["user"]["id"]
        async with client_from(ip) as client:
            again = await client.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
        assert again.status_code == 400, again.text
        assert again.json()["error"]["message"] == "Could not create account"
    finally:
        await cleanup([email], user_id)


@pytest.mark.asyncio
async def test_access_token_expires_in_12_hours_and_carries_token_version():
    email = f"token-exp-{uuid.uuid4().hex}@example.com"
    user_id = None
    try:
        body = await signup(email, random_ip())
        user_id = body["user"]["id"]
        claims = jwt.decode(body["access_token"], options={"verify_signature": False})
        expected = datetime.now(UTC) + timedelta(hours=12)
        assert abs(claims["exp"] - expected.timestamp()) < 120
        assert claims.get("tv") == 0
    finally:
        await cleanup([email], user_id)


@pytest.mark.parametrize("hours", [0, 169])
def test_jwt_expire_hours_is_validated(hours):
    with pytest.raises(ValidationError):
        SettingsV2(debug=True, jwt_secret="debug", jwt_expire_hours=hours)


@pytest.mark.asyncio
async def test_logout_all_revokes_existing_tokens():
    email = f"logout-all-{uuid.uuid4().hex}@example.com"
    ip = random_ip()
    user_id = None
    try:
        body = await signup(email, ip)
        user_id = body["user"]["id"]
        old_headers = {"Authorization": f"Bearer {body['access_token']}"}
        async with client_from(ip) as client:
            logout = await client.post("/api/auth/logout-all", headers=old_headers)
            after = await client.get("/api/dashboard/stats", headers=old_headers)
            assert after.status_code == 401, after.text
            assert logout.status_code == 200, logout.text

            relogin = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
            assert relogin.status_code == 200, relogin.text
            new_headers = {"Authorization": f"Bearer {relogin.json()['access_token']}"}
            fresh = await client.get("/api/dashboard/stats", headers=new_headers)
            assert fresh.status_code == 200, fresh.text
    finally:
        await cleanup([email], user_id)
