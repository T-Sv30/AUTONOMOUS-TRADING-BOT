import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app_state import db
from auth_core import verify_token
from services.api_errors_v2 import error_envelope
from services.authorization_v2 import Scope, effective_scopes, has_scope
from services.live_pre_submit_safety_service_v2 import LivePreSubmitSafetyServiceV2
from services.request_context_v2 import get_request_id

LIVE_ORDER_PATHS = {"/api/live-trading/market-buy", "/api/live-trading/market-sell"}


class ScopeEnforcementMiddlewareV2(BaseHTTPMiddleware):
    """Central authorization and safety choke point for live and privileged ops routes.

    The signed approval challenge is verified and consumed by LiveTradingServiceV2,
    not here, so every caller of the service is covered and a challenge is consumed once.
    """

    def required_scope_for(self, method: str, path: str, body: dict | None) -> str | None:
        if path.startswith("/api/live-readonly/"):
            return Scope.TRADING_LIVE_PREVIEW.value

        if path in {"/api/live-trading/gate", "/api/live-trading/audits"}:
            return Scope.TRADING_LIVE_PREVIEW.value

        if path == "/api/live-approvals/challenge":
            return Scope.TRADING_LIVE_EXECUTE.value

        if path in LIVE_ORDER_PATHS and method == "POST":
            if self.is_non_dry_run_live_order(method, path, body):
                return Scope.TRADING_LIVE_EXECUTE.value
            return Scope.TRADING_LIVE_PREVIEW.value

        if path == "/api/ops/readiness":
            return Scope.OPS_READINESS.value

        if path == "/api/ops/indexes/ensure":
            return Scope.OPS_INDEXES.value

        if path == "/api/ops/emergency-halt":
            return Scope.OPS_HALT.value

        return None

    @staticmethod
    def is_non_dry_run_live_order(method: str, path: str, body: dict | None) -> bool:
        # Fail closed: any dry_run value other than literal true counts as a live order here.
        # The request model then rejects non-boolean values with 422.
        return method == "POST" and path in LIVE_ORDER_PATHS and isinstance(body, dict) and "dry_run" in body and body["dry_run"] is not True

    @staticmethod
    async def parse_json_body(request: Request) -> tuple[dict | None, bytes]:
        body_bytes = await request.body()
        if not body_bytes:
            return None, body_bytes
        try:
            parsed = json.loads(body_bytes.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None, body_bytes
        except json.JSONDecodeError:
            return None, body_bytes

    @staticmethod
    def restore_body(request: Request, body: bytes) -> None:
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive

    @staticmethod
    def auth_error(status_code: int, code: str, message: str) -> JSONResponse:
        request_id = get_request_id()
        return JSONResponse(
            status_code=status_code,
            content=error_envelope(code=code, message=message, request_id=request_id, details={"status_code": status_code}),
            headers={"X-Request-ID": request_id} if request_id else {},
        )

    async def load_user(self, request: Request) -> dict | None:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        payload = verify_token(token)
        if not payload or "user_id" not in payload:
            return None
        user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0})
        if not user:
            return None
        if not user.get("roles"):
            user["roles"] = ["user"]
        if "scopes" not in user:
            user["scopes"] = []
        user["effective_scopes"] = sorted(effective_scopes(user))
        return user

    async def verify_live_pre_submit_safety(self, user: dict) -> JSONResponse | None:
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user["id"])
        if not result.get("allowed"):
            return self.auth_error(409, "CONFLICT", str(result.get("reason", "live pre-submit safety check failed")))
        return None

    async def dispatch(self, request: Request, call_next):
        parsed_body = None
        raw_body = b""
        if request.method in {"POST", "PUT", "PATCH"}:
            parsed_body, raw_body = await self.parse_json_body(request)
            self.restore_body(request, raw_body)

        required_scope = self.required_scope_for(request.method, request.url.path, parsed_body)
        if not required_scope:
            return await call_next(request)

        user = await self.load_user(request)
        if not user:
            return self.auth_error(401, "UNAUTHORIZED", "Authentication required")

        if not has_scope(user, required_scope):
            return self.auth_error(403, "FORBIDDEN", f"Missing required scope: {required_scope}")

        if self.is_non_dry_run_live_order(request.method, request.url.path, parsed_body):
            safety_error = await self.verify_live_pre_submit_safety(user)
            if safety_error:
                return safety_error

        request.state.authorized_user = user
        return await call_next(request)
