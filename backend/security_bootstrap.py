"""Runtime hardening hooks for the FastAPI app.

This module is intentionally small and imported from server.py after the app and
routes are defined. It patches unsafe defaults without requiring a large server
rewrite.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from jwt import ExpiredSignatureError, InvalidTokenError
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from runtime_config import CORS_ORIGINS, JWT_SECRET

AUTH_REQUIRED_PREFIXES = (
    "/api/market-analysis",
    "/api/market-data/",
)


def _server_module():
    module = sys.modules.get("server") or sys.modules.get("backend.server")
    if module is None:
        raise RuntimeError("security_bootstrap must be imported from server.py after app creation")
    return module


def _create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=7)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm="HS256")


def _verify_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except ExpiredSignatureError:
        return None
    except InvalidTokenError:
        return None


def _replace_cors_middleware(app) -> None:
    app.user_middleware = [
        item for item in app.user_middleware
        if item.cls is not CORSMiddleware
    ]
    app.user_middleware.insert(
        0,
        Middleware(
            CORSMiddleware,
            allow_credentials=True,
            allow_origins=CORS_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        ),
    )
    app.middleware_stack = None


def _install_market_endpoint_auth(app, verify_token: Callable[[str], dict | None]) -> None:
    @app.middleware("http")
    async def require_auth_for_market_endpoints(request: Request, call_next):
        path = request.url.path
        if path.startswith(AUTH_REQUIRED_PREFIXES):
            auth_header = request.headers.get("authorization", "")
            scheme, _, token = auth_header.partition(" ")
            if scheme.lower() != "bearer" or not token or not verify_token(token):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)


def harden_server() -> None:
    module = _server_module()
    module.create_access_token = _create_access_token
    module.verify_token = _verify_token
    _replace_cors_middleware(module.app)
    _install_market_endpoint_auth(module.app, _verify_token)


harden_server()
