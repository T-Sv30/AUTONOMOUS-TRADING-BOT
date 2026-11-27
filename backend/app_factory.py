from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

import app_state
from api_routes import api_router
from app_state import lifespan
from runtime_config import CORS_ORIGINS
from services.api_errors_v2 import http_exception_handler, unhandled_exception_handler, validation_exception_handler
from services.operational_readiness_v2 import OperationalReadinessServiceV2
from services.request_context_v2 import RequestContextMiddlewareV2
from services.scope_enforcement_middleware_v2 import ScopeEnforcementMiddlewareV2
from services.security_headers_v2 import SecurityHeadersMiddlewareV2
from services.worker_heartbeat_service_v2 import WorkerHeartbeatServiceV2

SERVICE_NAME = "Autonomous Trading Bot"


async def fresh_worker_heartbeat(db) -> bool:
    try:
        workers = (await WorkerHeartbeatServiceV2(db).list_workers())["workers"]
    except Exception:
        return False
    return any(not worker.get("stale") and worker.get("status") not in {"stopped", "stopping"} for worker in workers)


async def readiness_failures(strict: bool) -> list[str]:
    """Names of the failed readiness checks. Only names leave the process: no settings, errors or secrets."""
    service = OperationalReadinessServiceV2()
    db = app_state.db  # read at request time so tests and ops tooling can swap the database
    failed: list[str] = []
    if strict:
        report = await service.readiness(db, strict=True)
        database_ready = report["database"]["status"] == "ready"
        settings_ready = report["environment"]["status"] == "ready"
    else:
        database_ready = (await service.database_health(db))["status"] == "ready"
        settings_ready = service.validate_environment(strict=False)["summary"]["failed_critical"] == 0
    if not database_ready:
        failed.append("database")
    if not settings_ready:
        failed.append("settings")
    if strict and not (database_ready and await fresh_worker_heartbeat(db)):
        failed.append("worker_heartbeat")
    return failed


def create_app() -> FastAPI:
    app = FastAPI(title="Autonomous Trading Bot", lifespan=lifespan)

    @app.get("/")
    async def root():
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/readyz")
    async def readyz(strict: bool = False):
        failed = await readiness_failures(strict)
        if failed:
            return JSONResponse(status_code=503, content={"status": "not_ready", "service": SERVICE_NAME, "strict": strict, "failed_checks": failed})
        return {"status": "ready", "service": SERVICE_NAME, "strict": strict}

    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(api_router)
    app.add_middleware(SecurityHeadersMiddlewareV2)
    app.add_middleware(ScopeEnforcementMiddlewareV2)
    app.add_middleware(RequestContextMiddlewareV2)
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    return app
