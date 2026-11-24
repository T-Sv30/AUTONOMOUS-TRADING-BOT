"""Process-level safety bootstrap for the backend.

Python imports this module automatically during interpreter startup when the
backend directory is on sys.path. It forces runtime configuration validation,
locks CORS arguments, and requires bearer auth for market endpoints without a
large rewrite of server.py.
"""

from __future__ import annotations

from collections.abc import Iterable

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from runtime_config import CORS_ORIGINS, JWT_SECRET

_PROTECTED_PREFIXES: tuple[str, ...] = (
    "/api/market-analysis",
    "/api/market-data/",
)

_ORIGINAL_ADD_MIDDLEWARE = FastAPI.add_middleware
_ORIGINAL_INCLUDE_ROUTER = FastAPI.include_router


def _is_cors(cls: type) -> bool:
    return cls is CORSMiddleware or getattr(cls, "__name__", "") == "CORSMiddleware"


def _patch_cors_options(options: dict) -> dict:
    patched = dict(options)
    patched["allow_credentials"] = True
    patched["allow_origins"] = CORS_ORIGINS
    patched["allow_methods"] = ["GET", "POST", "OPTIONS"]
    patched["allow_headers"] = ["Authorization", "Content-Type"]
    return patched


def _verify_bearer(header_value: str | None) -> bool:
    if not header_value:
        return False
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except Exception:
        return False


def _path_is_protected(path: str, prefixes: Iterable[str] = _PROTECTED_PREFIXES) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def _install_endpoint_guard(app: FastAPI) -> None:
    if getattr(app.state, "phase1_market_endpoint_guard", False):
        return

    @app.middleware("http")
    async def require_market_endpoint_auth(request: Request, call_next):
        if _path_is_protected(request.url.path):
            if not _verify_bearer(request.headers.get("authorization")):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)

    app.state.phase1_market_endpoint_guard = True


def _hardened_add_middleware(self: FastAPI, middleware_class, *args, **kwargs):
    if _is_cors(middleware_class):
        kwargs = _patch_cors_options(kwargs)
    return _ORIGINAL_ADD_MIDDLEWARE(self, middleware_class, *args, **kwargs)


def _hardened_include_router(self: FastAPI, *args, **kwargs):
    result = _ORIGINAL_INCLUDE_ROUTER(self, *args, **kwargs)
    _install_endpoint_guard(self)
    return result


FastAPI.add_middleware = _hardened_add_middleware
FastAPI.include_router = _hardened_include_router
