import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_backend_python(code: str, env_updates: dict[str, str | None]):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)
    for key, value in env_updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_runtime_config_requires_jwt_secret_outside_debug():
    result = run_backend_python(
        "import runtime_config",
        {
            "DEBUG": "False",
            "JWT_SECRET": None,
            "CORS_ORIGINS": "http://localhost:3000",
        },
    )

    assert result.returncode != 0
    assert "JWT_SECRET must be configured" in result.stderr


def test_runtime_config_rejects_wildcard_cors_outside_debug():
    result = run_backend_python(
        "import runtime_config",
        {
            "DEBUG": "False",
            "JWT_SECRET": "phase1-hardening-test-secret-with-more-than-32-chars",
            "CORS_ORIGINS": "*",
        },
    )

    assert result.returncode != 0
    assert "Wildcard CORS" in result.stderr


def test_create_app_cors_middleware_uses_validated_settings():
    from starlette.middleware.cors import CORSMiddleware

    from app_factory import create_app
    from services.settings_v2 import SETTINGS

    cors = [middleware for middleware in create_app().user_middleware if middleware.cls is CORSMiddleware]

    assert len(cors) == 1
    assert cors[0].kwargs["allow_origins"] == SETTINGS.cors_origins
    assert cors[0].kwargs["allow_methods"] == ["GET", "POST", "OPTIONS"]
    assert "Authorization" in cors[0].kwargs["allow_headers"]


async def test_market_data_route_requires_a_token():
    import httpx

    from app_factory import create_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver") as client:
        response = await client.get("/api/market-data/BTC-USD")

    assert response.status_code == 401, response.text
