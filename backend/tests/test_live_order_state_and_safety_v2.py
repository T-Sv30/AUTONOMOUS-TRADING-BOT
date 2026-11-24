import os
from datetime import UTC, datetime

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-live-state-tests-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("LIVE_REQUIRE_FRESH_RECONCILIATION", "True")
os.environ.setdefault("LIVE_MAX_RECONCILIATION_AGE_SECONDS", "300")

import pytest

from app_state import db
from services.live_order_state_service_v2 import LiveOrderState, LiveOrderStateError, LiveOrderStateServiceV2
from services.live_pre_submit_safety_service_v2 import LivePreSubmitSafetyServiceV2


async def cleanup(user_id: str):
    await db.live_order_transitions.delete_many({"user_id": user_id})
    await db.live_readonly_reports.delete_many({"user_id": user_id})
    await db.live_halts.delete_many({"user_id": user_id})
    await db.live_halts.delete_many({"scope": "global"})


@pytest.mark.asyncio
async def test_live_order_state_machine_hash_chain_and_invalid_transition_detection():
    user_id = "state-user-1"
    service = LiveOrderStateServiceV2(db)
    try:
        created = await service.create_order(user_id=user_id, symbol="BTC-USD", side="BUY", notional_usd=5, request_id="req-1")
        assert created["state"] == LiveOrderState.REQUESTED.value
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.GATE_CHECKED.value, reason="gate ok")
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.RISK_CHECKED.value, reason="risk ok")
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.APPROVAL_REQUIRED.value, reason="approval required")
        await service.transition(order_id=created["order_id"], next_state=LiveOrderState.APPROVED.value, reason="approved")

        verified = await service.verify_chain(created["order_id"])
        assert verified["status"] == "ok"
        assert verified["checked_records"] == 5

        with pytest.raises(LiveOrderStateError):
            await service.transition(order_id=created["order_id"], next_state=LiveOrderState.RECONCILED.value, reason="invalid skip")
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_blocks_without_fresh_reconciliation():
    user_id = "safety-user-no-recon"
    try:
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
        assert result["allowed"] is False
        assert "fresh live-readonly reconciliation" in result["reason"]
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_allows_with_fresh_reconciliation_and_no_unresolved_order():
    user_id = "safety-user-fresh"
    try:
        await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
        assert result["allowed"] is True
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_blocks_unresolved_live_order():
    user_id = "safety-user-unresolved"
    states = LiveOrderStateServiceV2(db)
    try:
        await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
        await states.create_order(user_id=user_id, symbol="BTC-USD", side="BUY", notional_usd=5)
        result = await LivePreSubmitSafetyServiceV2(db).safe_check(user_id)
        assert result["allowed"] is False
        assert "unresolved live order" in result["reason"]
    finally:
        await cleanup(user_id)


@pytest.mark.asyncio
async def test_pre_submit_safety_blocks_active_halt():
    user_id = "safety-user-halt"
    safety = LivePreSubmitSafetyServiceV2(db)
    try:
        await db.live_readonly_reports.insert_one({"user_id": user_id, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
        await safety.create_halt(user_id=user_id, scope="user", reason="test halt", created_by="pytest")
        result = await safety.safe_check(user_id)
        assert result["allowed"] is False
        assert "halt" in result["reason"]
    finally:
        await cleanup(user_id)
