"""M1.5b task 17 (P0-18): performance metrics use the account's starting cash and count round trips."""

import os

os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-secret-for-performance-metrics-more-than-32-chars")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("MARKET_DATA_SOURCE", "simulated")
os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("RUNTIME_ROLE", "api")
os.environ.setdefault("API_EMBED_BOT_MANAGER", "false")
os.environ.setdefault("RUN_MONGO_INDEX_BOOTSTRAP", "false")

import pytest
from fakes import FakeDB

import api_routes_v2

USER = {"id": "u1"}


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(api_routes_v2, "db", db)
    return db


def seed_account(db, equity, starting_cash=None):
    state = {"user_id": "u1", "cash_balance": equity}
    if starting_cash is not None:
        state["starting_cash"] = starting_cash
    db.portfolio_state.docs.append(state)
    db.risk_metrics.docs.append({"user_id": "u1", "total_equity": equity, "timestamp": "2026-11-27T00:00:00+00:00"})


def trade(side, status="filled", realized_pnl=None, minute=0):
    doc = {"user_id": "u1", "symbol": "BTC-USD", "side": side, "status": status, "created_at": f"2026-11-26T12:{minute:02d}:00+00:00"}
    if realized_pnl is not None:
        doc["realized_pnl"] = realized_pnl
    return doc


@pytest.mark.asyncio
async def test_roi_uses_the_account_starting_cash(fake_db):
    seed_account(fake_db, equity=5500.0, starting_cash=5000.0)
    metrics = await api_routes_v2.get_performance_metrics(current_user=USER)
    assert metrics["roi"] == 10.0


@pytest.mark.asyncio
async def test_roi_falls_back_to_ten_thousand_for_a_legacy_account(fake_db):
    seed_account(fake_db, equity=11000.0)
    metrics = await api_routes_v2.get_performance_metrics(current_user=USER)
    assert metrics["roi"] == 10.0


@pytest.mark.asyncio
async def test_win_rate_counts_round_trips(fake_db):
    seed_account(fake_db, equity=10060.0, starting_cash=10000.0)
    fake_db.trades_v2.docs.extend([
        trade("BUY", minute=0),
        trade("SELL", realized_pnl=100.0, minute=1),
        trade("BUY", minute=2),
        trade("SELL", realized_pnl=-40.0, minute=3),
    ])
    metrics = await api_routes_v2.get_performance_metrics(current_user=USER)
    assert metrics["win_rate"] == 50.0
    assert metrics["total_trades"] == 4
    assert (metrics["winning_trades"], metrics["losing_trades"]) == (1, 1)
    assert metrics["net_pnl"] == 60.0


@pytest.mark.asyncio
async def test_rejected_and_pending_trades_are_not_counted(fake_db):
    seed_account(fake_db, equity=10100.0, starting_cash=10000.0)
    fake_db.trades_v2.docs.extend([
        trade("BUY", minute=0),
        trade("SELL", realized_pnl=100.0, minute=1),
        trade("SELL", status="rejected", minute=2),
        trade("BUY", status="pending", minute=3),
        trade("BUY", status="partially_filled", minute=4),
    ])
    metrics = await api_routes_v2.get_performance_metrics(current_user=USER)
    assert metrics["total_trades"] == 3
    assert metrics["win_rate"] == 100.0
