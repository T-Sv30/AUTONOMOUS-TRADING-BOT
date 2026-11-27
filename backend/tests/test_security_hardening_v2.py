import os

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-security-tests")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")

import pytest
from fakes import FakeDB
from fastapi import HTTPException

import auth_core
from runtime_config import validate_cors_origins, validate_jwt_secret
from services.live_trading_gate_v2 import LiveTradingGateV2


@pytest.mark.parametrize("secret", ["short", "secret", "local-debug-placeholder"])
def test_production_jwt_secret_validation_rejects_weak_values(secret):
    with pytest.raises(RuntimeError):
        validate_jwt_secret(secret, debug=False)


def test_production_jwt_secret_validation_accepts_long_secret():
    validate_jwt_secret("x" * 40, debug=False)


def test_cors_validation_rejects_wildcard_in_production():
    with pytest.raises(RuntimeError):
        validate_cors_origins(["*"], debug=False)


def test_cors_validation_rejects_plain_http_non_localhost_in_production():
    with pytest.raises(RuntimeError):
        validate_cors_origins(["http://example.com"], debug=False)


def test_cors_validation_accepts_https_origin_in_production():
    validate_cors_origins(["https://example.com"], debug=False)


def test_live_approval_token_comparison():
    assert LiveTradingGateV2.approval_tokens_match("approve-me", "approve-me") is True
    assert LiveTradingGateV2.approval_tokens_match("wrong", "approve-me") is False
    assert LiveTradingGateV2.approval_tokens_match(None, "approve-me") is False
    assert LiveTradingGateV2.approval_tokens_match("approve-me", None) is False


@pytest.mark.asyncio
async def test_auth_throttle_blocks_after_failure_limit(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(auth_core, "db", fake_db)
    email = "user@example.com"

    for _ in range(auth_core.AUTH_FAILURE_LIMIT):
        await auth_core.record_auth_failure(email, "10.0.0.1")

    with pytest.raises(HTTPException) as exc:
        await auth_core.enforce_auth_throttle(email, "10.0.0.1")

    assert exc.value.status_code == 429
    # Another IP with no failures of its own is not locked out.
    await auth_core.enforce_auth_throttle(email, "10.0.0.2")


@pytest.mark.asyncio
async def test_auth_throttle_clears_failures(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(auth_core, "db", fake_db)
    email = "user@example.com"

    await auth_core.record_auth_failure(email, "10.0.0.1")
    assert await auth_core.recent_auth_failures(email, "10.0.0.1") == 1

    await auth_core.clear_auth_failures(email, "10.0.0.1")
    assert await auth_core.recent_auth_failures(email, "10.0.0.1") == 0
