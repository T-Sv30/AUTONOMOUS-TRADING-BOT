import os
import uuid

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-e2e-paper-smoke-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")
os.environ.setdefault("COINBASE_LIVE_ORDER_KILL_SWITCH", "true")
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("LIVE_EXECUTION_ADAPTER", "disabled")

import httpx
import pytest
from pymongo.errors import DuplicateKeyError

from app_factory import create_app
from app_state import db
from services.bot_engine import BotEngine
from services.ledger_service_v2 import LedgerServiceV2
from services.mongo_indexes_v2 import MongoIndexServiceV2
from services.portfolio_service_v2 import PortfolioServiceV2


async def cleanup_user(user_id: str | None = None, email: str | None = None) -> None:
    if email:
        await db.users.delete_many({"email": email})
    if not user_id:
        return
    for collection_name in [
        "users",
        "bot_configs",
        "portfolio_state",
        "positions_v2",
        "trades_v2",
        "ledger_entries",
        "reconciliation_reports",
        "risk_metrics",
        "execution_locks",
        "alerts",
        "auth_failures",
        "live_order_audits",
        "live_readonly_reports",
    ]:
        await getattr(db, collection_name).delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_e2e_paper_smoke_api_auth_controlled_execution_reconciliation_and_idempotency():
    """End-to-end paper smoke test.

    This test intentionally avoids waiting on the background worker. It exercises
    the production API for auth/config and then invokes the same paper execution
    path that BotEngine uses for a controlled deterministic order. That keeps CI
    fast and proves the critical paper-mode invariant:

    API auth/config -> BotEngine paper execution -> trade -> position -> ledger
    -> reconciliation, with duplicate execution blocked across engine instances.
    """

    app = create_app()
    email = f"paper-smoke-{uuid.uuid4().hex}@example.com"
    password = "SmokeTestPassword123!"
    user_id = None

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            signup = await client.post("/api/auth/signup", json={"email": email, "password": password})
            assert signup.status_code == 200, signup.text
            signup_body = signup.json()
            token = signup_body["access_token"]
            user_id = signup_body["user"]["id"]
            headers = {"Authorization": f"Bearer {token}"}

            dashboard = await client.get("/api/dashboard/stats", headers=headers)
            assert dashboard.status_code == 200, dashboard.text
            assert dashboard.json()["cash_balance"] == 10000.0

            started = await client.post("/api/bot/start", headers=headers)
            assert started.status_code == 200, started.text
            assert started.json()["is_active"] is True

            config = await client.get("/api/bot-config", headers=headers)
            assert config.status_code == 200, config.text
            assert config.json()["is_active"] is True

        await db.bot_configs.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "is_active": True,
                    "symbols": ["BTC-USD"],
                    "capital_floor": 0.97,
                    "max_daily_loss": 0.015,
                    "risk_target_vol": 0.10,
                    "live_trading_enabled": False,
                }
            },
            upsert=True,
        )

        context = {
            "action": "BUY",
            "selected": {"strategy": "e2e_paper_smoke"},
            "notional": 50.0,
            "reason": "deterministic e2e smoke test",
            "timeframe": "1m",
            "decision_candle_close": "2026-11-26T12:00:00+00:00",
        }

        engine = BotEngine(db)
        await engine.execute_buy(user_id, "BTC-USD", 50.0, context)

        trades = await db.trades_v2.find({"user_id": user_id}, {"_id": 0}).to_list(100)
        assert len(trades) == 1
        trade = trades[0]
        assert trade["status"] == "filled"
        assert trade["side"] == "BUY"
        assert trade["symbol"] == "BTC-USD"
        assert trade["paper_execution"] is True
        assert trade["simulation"] is True
        assert trade["execution_adapter"] == "paper_v2"
        assert trade["idempotency_key"] == f"{user_id}:BTC-USD:BUY:e2e_paper_smoke:1m:2026-11-26T12:00:00+00:00"

        positions = await db.positions_v2.find({"user_id": user_id}, {"_id": 0}).to_list(100)
        assert len(positions) == 1
        position = positions[0]
        assert position["symbol"] == "BTC-USD"
        assert position["base_units"] > 0
        assert position["notional_usd"] > 0

        ledger_entries = await db.ledger_entries.find({"user_id": user_id}, {"_id": 0}).to_list(100)
        assert {entry["event_type"] for entry in ledger_entries} == {"BUY_FILL", "FEE"}

        report = await LedgerServiceV2(db).reconcile(user_id, starting_cash=10000.0)
        assert report["status"] == "ok", report
        assert report["issues"] == []

        restarted_engine = BotEngine(db)
        await restarted_engine.execute_buy(user_id, "BTC-USD", 50.0, context)

        trades_after_replay = await db.trades_v2.find({"user_id": user_id}, {"_id": 0}).to_list(100)
        ledger_after_replay = await db.ledger_entries.find({"user_id": user_id}, {"_id": 0}).to_list(100)
        assert len(trades_after_replay) == 1
        assert len(ledger_after_replay) == len(ledger_entries)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            stopped = await client.post("/api/bot/stop", headers=headers)
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["is_active"] is False

    finally:
        await cleanup_user(user_id=user_id, email=email)


@pytest.mark.asyncio
async def test_e2e_mongo_rejects_second_non_rejected_trade_with_same_idempotency_key():
    """The unique partial index on trades_v2 is the backstop behind the lock and already_executed."""
    await MongoIndexServiceV2(db).ensure_indexes()
    user_id = f"paper-smoke-dup-{uuid.uuid4().hex}"
    portfolio = PortfolioServiceV2(db)
    key = f"{user_id}:BTC-USD:BUY:e2e_paper_smoke:1m:2026-11-26T12:00:00+00:00"
    order = {"status": "filled", "client_order_id": "dup-1", "idempotency_key": key, "simulation": True}

    try:
        await portfolio.record_trade_attempt(user_id, "BTC-USD", "BUY", order)
        with pytest.raises(DuplicateKeyError):
            await portfolio.record_trade_attempt(user_id, "BTC-USD", "BUY", {**order, "client_order_id": "dup-2"})
        assert await db.trades_v2.count_documents({"user_id": user_id}) == 1
    finally:
        await cleanup_user(user_id=user_id)
