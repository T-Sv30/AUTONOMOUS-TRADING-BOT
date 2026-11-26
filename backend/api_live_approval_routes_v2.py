
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from api_routes_v2 import api_router
from app_state import db
from auth_core import get_current_user
from services.authz_dependencies_v2 import require_live_execute
from services.live_approval_challenge_service_v2 import LiveApprovalChallengeError, LiveApprovalChallengeServiceV2


class LiveApprovalChallengeRequest(BaseModel):
    side: str = Field(pattern="^(BUY|SELL)$")
    symbol: str = "BTC-USD"
    notional_usd: float | None = Field(default=None, gt=0)
    base_units: float | None = Field(default=None, gt=0)
    reference_price: float | None = Field(default=None, gt=0)
    expires_in_seconds: int = Field(default=300, ge=30, le=900)


@api_router.post("/live-approvals/challenge")
async def create_live_approval_challenge(
    request: LiveApprovalChallengeRequest,
    current_user: dict = Depends(require_live_execute),
):
    try:
        challenge = await LiveApprovalChallengeServiceV2(db).create_challenge(
            user_id=current_user["id"],
            side=request.side,
            symbol=request.symbol,
            notional_usd=request.notional_usd,
            base_units=request.base_units,
            reference_price=request.reference_price,
            expires_in_seconds=request.expires_in_seconds,
        )
    except LiveApprovalChallengeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return challenge


@api_router.get("/live-approvals")
async def list_live_approval_challenges(current_user: dict = Depends(get_current_user), limit: int = 50):
    challenges = await db.live_approval_challenges.find(
        {"user_id": current_user["id"]},
        {"_id": 0, "approval_token_hash": 0, "nonce": 0},  # projection excluding the hash, not a password  # nosec B105
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"challenges": challenges}
