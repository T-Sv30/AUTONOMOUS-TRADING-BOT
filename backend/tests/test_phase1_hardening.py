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


def test_sitecustomize_forces_explicit_cors_options():
    result = run_backend_python(
        """
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

cors = app.user_middleware[0]
assert cors.kwargs["allow_origins"] == ["http://localhost:3000"]
assert cors.kwargs["allow_methods"] == ["GET", "POST", "OPTIONS"]
assert cors.kwargs["allow_headers"] == ["Authorization", "Content-Type"]
""",
        {
            "DEBUG": "False",
            "JWT_SECRET": "phase1-hardening-test-secret-with-more-than-32-chars",
            "CORS_ORIGINS": "http://localhost:3000",
        },
    )

    assert result.returncode == 0, result.stderr


def test_sitecustomize_requires_auth_for_market_endpoints():
    result = run_backend_python(
        """
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
router = APIRouter()

@router.get("/api/market-data/BTC-USD")
async def market_data():
    return {"ok": True}

app.include_router(router)
client = TestClient(app)
response = client.get("/api/market-data/BTC-USD")
assert response.status_code == 401
""",
        {
            "DEBUG": "False",
            "JWT_SECRET": "phase1-hardening-test-secret-with-more-than-32-chars",
            "CORS_ORIGINS": "http://localhost:3000",
        },
    )

    assert result.returncode == 0, result.stderr
