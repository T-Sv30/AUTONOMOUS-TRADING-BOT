import os

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-request-error-tests-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("SIMULATION_MODE", "True")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

import httpx
import pytest

from app_factory import create_app
from services.request_context_v2 import REQUEST_ID_HEADER


@pytest.mark.asyncio
async def test_request_id_header_is_propagated_on_success_response():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    request_id = "paper-smoke-request-123"

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/healthz", headers={REQUEST_ID_HEADER: request_id})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == request_id
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_invalid_request_id_header_is_replaced():
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/healthz", headers={REQUEST_ID_HEADER: "bad value with spaces"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]
    assert response.headers[REQUEST_ID_HEADER] != "bad value with spaces"


@pytest.mark.asyncio
async def test_http_exception_uses_standard_error_envelope_and_request_id():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    request_id = "auth-error-123"

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/dashboard/stats", headers={REQUEST_ID_HEADER: request_id})

    # FastAPI's HTTPBearer returns 401 (not 403) for a missing token since 0.12x.
    assert response.status_code == 401
    assert response.headers[REQUEST_ID_HEADER] == request_id
    payload = response.json()
    assert payload == {
        "error": {
            "code": "UNAUTHORIZED",
            "message": "Not authenticated",
            "request_id": request_id,
            "details": {"status_code": 401},
        }
    }


@pytest.mark.asyncio
async def test_validation_exception_uses_standard_error_envelope_and_request_id():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    request_id = "validation-error-123"

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/auth/signup",
            headers={REQUEST_ID_HEADER: request_id},
            json={"email": "not-an-email", "password": "short"},
        )

    assert response.status_code == 422
    assert response.headers[REQUEST_ID_HEADER] == request_id
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["message"] == "Request validation failed"
    assert payload["error"]["request_id"] == request_id
    assert "errors" in payload["error"]["details"]
    assert payload["error"]["details"]["errors"]
