import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from services.request_context_v2 import get_request_id
from services.structured_logging import log_event

logger = logging.getLogger(__name__)


def error_code_for_status(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }.get(status_code, "HTTP_ERROR")


def error_envelope(*, code: str, message: str, request_id: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        }
    }


def request_id_from(request: Request) -> str:
    return getattr(request.state, "request_id", None) or get_request_id()


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = request_id_from(request)
    message = str(exc.detail) if exc.detail else "HTTP error"
    payload = error_envelope(
        code=error_code_for_status(exc.status_code),
        message=message,
        request_id=request_id,
        details={"status_code": exc.status_code},
    )
    headers = dict(exc.headers or {})
    headers["X-Request-ID"] = request_id
    log_event(
        logger,
        logging.WARNING if exc.status_code < 500 else logging.ERROR,
        "api_http_error",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=exc.status_code,
        code=payload["error"]["code"],
    )
    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


def json_safe_validation_errors(errors) -> list[dict[str, Any]]:
    # A field_validator's ValueError lands in ctx["error"] as the exception object, which JSON cannot encode.
    return [{**error, "ctx": {key: str(value) for key, value in error["ctx"].items()}} if "ctx" in error else dict(error) for error in errors]


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = request_id_from(request)
    payload = error_envelope(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        request_id=request_id,
        details={"errors": json_safe_validation_errors(exc.errors())},
    )
    log_event(
        logger,
        logging.WARNING,
        "api_validation_error",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=payload,
        headers={"X-Request-ID": request_id},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_from(request)
    payload = error_envelope(
        code="INTERNAL_SERVER_ERROR",
        message="Internal server error",
        request_id=request_id,
        details={},
    )
    log_event(
        logger,
        logging.ERROR,
        "api_unhandled_error",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        error=str(exc),
    )
    return JSONResponse(status_code=500, content=payload, headers={"X-Request-ID": request_id})
