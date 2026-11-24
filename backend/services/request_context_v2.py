import contextvars
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = contextvars.ContextVar("request_id", default="")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def normalize_request_id(value: str | None) -> str:
    if value and _VALID_REQUEST_ID.match(value):
        return value
    return uuid.uuid4().hex


def get_request_id() -> str:
    return _REQUEST_ID.get() or ""


def set_request_id(request_id: str):
    return _REQUEST_ID.set(request_id)


def reset_request_id(token) -> None:
    _REQUEST_ID.reset(token)


class RequestContextMiddlewareV2(BaseHTTPMiddleware):
    """Attach a request id to every request/response and context-aware log."""

    async def dispatch(self, request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = set_request_id(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)
