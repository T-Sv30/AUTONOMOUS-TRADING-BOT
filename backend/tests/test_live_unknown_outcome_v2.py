"""P0-8: a live order whose outcome is unknown is recorded, halted and resolved, never retried blind."""

from datetime import UTC, datetime

import pytest
from fakes import FakeDB

from services.coinbase_live_execution_adapter_v2 import CoinbaseLiveExecutionAdapterV2, CoinbaseLiveExecutionError
from services.live_approval_challenge_service_v2 import LiveApprovalChallengeServiceV2
from services.live_order_state_service_v2 import LiveOrderStateServiceV2
from services.live_trading_gate_v2 import LiveTradingGateConfig, LiveTradingGateV2
from services.live_trading_service_v2 import LiveTradingServiceV2

USER = "unknown-outcome-user"


class NotJSON(ValueError):
    pass


class FakeResponse:
    def __init__(self, status: int, body):
        self.status = status
        self.body = body

    async def json(self, *args, **kwargs):
        if isinstance(self.body, str):
            raise NotJSON(f"Attempt to decode JSON with unexpected mimetype: text/html ({self.body[:20]})")
        return self.body


class FakeRequest:
    """Async context manager like aiohttp's request: the outcome lands in __aenter__."""

    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *exc):
        return False


class FakeExchange:
    """Scripted Coinbase stand-in. Records every session, POST and GET."""

    def __init__(self, post_outcome, get_outcome=None):
        self.post_outcome = post_outcome
        self.get_outcome = get_outcome
        self.session_kwargs = []
        self.posts = []
        self.gets = []

    def session_factory(self, **kwargs):
        self.session_kwargs.append(kwargs)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, headers=None, data=None):
        self.posts.append(url)
        return FakeRequest(self.post_outcome)

    def get(self, url, headers=None):
        self.gets.append(url)
        return FakeRequest(self.get_outcome)


def build_service(monkeypatch, exchange: FakeExchange):
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", "false")
    monkeypatch.delenv("LIVE_ORDER_TIMEOUT_SECONDS", raising=False)
    fake_db = FakeDB()
    fake_db.bot_configs.docs.append({"user_id": USER, "live_trading_enabled": True})
    fake_db.live_readonly_reports.docs.append({"user_id": USER, "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
    adapter = CoinbaseLiveExecutionAdapterV2(api_key="key", api_secret="c2VjcmV0", passphrase="pass", session_factory=exchange.session_factory, max_retries=0)
    gate = LiveTradingGateV2(LiveTradingGateConfig(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2", signed_approval_required=True))
    return fake_db, LiveTradingServiceV2(fake_db, adapter=adapter, gate=gate)


async def place_buy(fake_db, service):
    challenge = await LiveApprovalChallengeServiceV2(fake_db).create_challenge(user_id=USER, side="BUY", symbol="BTC-USD", notional_usd=10.0)
    try:
        return await service.place_market_buy(USER, "BTC-USD", 10.0, approval_token=challenge["approval_token"], dry_run=False)
    except Exception as exc:
        # Keep going so the assertions show where the order ended, not only that it raised.
        return {"raised": exc}


def first_order_id(fake_db) -> str:
    return fake_db.live_order_transitions.docs[0]["order_id"]


async def state_of(fake_db, order_id: str) -> str:
    return (await LiveOrderStateServiceV2(fake_db).latest_transition(order_id))["state"]


def active_user_halts(fake_db):
    return [halt for halt in fake_db.live_halts.docs if halt.get("user_id") == USER and halt.get("active")]


TIMEOUT = TimeoutError("read timed out after the request was sent")
BAD_GATEWAY = FakeResponse(502, "<html><body>502 Bad Gateway</body></html>")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [TIMEOUT, BAD_GATEWAY], ids=["timeout", "502_html"])
async def test_unknown_outcome_is_recorded_halted_and_blocks_the_next_order(monkeypatch, outcome):
    exchange = FakeExchange(post_outcome=outcome)
    fake_db, service = build_service(monkeypatch, exchange)

    result = await place_buy(fake_db, service)
    order_id = first_order_id(fake_db)

    assert await state_of(fake_db, order_id) == "submit_unknown", result
    assert result["status"] == "submit_unknown"
    assert result["success"] is False
    assert result["live_order_id"] == order_id
    halts = active_user_halts(fake_db)
    assert [halt["reason"] for halt in halts] == [f"unknown order outcome {order_id}"]
    assert any(alert["severity"] == "critical" and alert["user_id"] == USER for alert in fake_db.alerts.docs)
    assert any(audit["status"] == "submit_unknown" for audit in fake_db.live_order_audits.docs)
    assert (await LiveOrderStateServiceV2(fake_db).verify_chain(order_id))["status"] == "ok"
    assert exchange.session_kwargs[0]["timeout"].total == 10

    second = await place_buy(fake_db, service)
    assert second["status"] == "pre_submit_blocked", second
    assert len(exchange.posts) == 1


@pytest.mark.asyncio
async def test_live_order_timeout_comes_from_env(monkeypatch):
    exchange = FakeExchange(post_outcome=TIMEOUT)
    fake_db, service = build_service(monkeypatch, exchange)
    monkeypatch.setenv("LIVE_ORDER_TIMEOUT_SECONDS", "3.5")

    await place_buy(fake_db, service)

    assert exchange.session_kwargs[0]["timeout"].total == 3.5


@pytest.mark.asyncio
async def test_4xx_json_rejection_ends_rejected_without_a_halt(monkeypatch):
    exchange = FakeExchange(post_outcome=FakeResponse(400, {"message": "Insufficient funds"}))
    fake_db, service = build_service(monkeypatch, exchange)

    result = await place_buy(fake_db, service)
    order_id = first_order_id(fake_db)

    assert await state_of(fake_db, order_id) == "rejected", result
    assert isinstance(result.get("raised"), CoinbaseLiveExecutionError)
    assert "Insufficient funds" in str(result["raised"])
    assert active_user_halts(fake_db) == []
    assert (await LiveOrderStateServiceV2(fake_db).verify_chain(order_id))["status"] == "ok"


async def unknown_order(monkeypatch, get_outcome):
    exchange = FakeExchange(post_outcome=TIMEOUT, get_outcome=get_outcome)
    fake_db, service = build_service(monkeypatch, exchange)
    await place_buy(fake_db, service)
    return exchange, fake_db, service, first_order_id(fake_db)


@pytest.mark.asyncio
async def test_resolve_finds_a_filled_order_by_client_oid_and_releases_the_halt(monkeypatch):
    filled = {"id": "ex-1", "status": "done", "filled_size": "0.0002", "executed_value": "10.0", "fill_fees": "0.01", "product_id": "BTC-USD", "side": "buy"}
    exchange, fake_db, service, order_id = await unknown_order(monkeypatch, FakeResponse(200, filled))
    client_oid = fake_db.live_order_transitions.docs[0]["client_order_id"]

    resolved = await service.resolve_unknown(USER, order_id)

    states = [doc["state"] for doc in sorted(fake_db.live_order_transitions.docs, key=lambda doc: doc["sequence"]) if doc["order_id"] == order_id]
    assert states[states.index("submit_unknown") + 1 :][:2] == ["acknowledged", "filled"]
    assert resolved["status"] == "filled"
    assert exchange.gets == [f"{service.adapter.base_url}/orders/client:{client_oid}"]
    assert len(exchange.posts) == 1  # never re-submitted
    assert active_user_halts(fake_db) == []
    assert (await LiveOrderStateServiceV2(fake_db).verify_chain(order_id))["status"] == "ok"


@pytest.mark.asyncio
async def test_resolve_marks_an_order_the_exchange_never_saw_as_failed(monkeypatch):
    exchange, fake_db, service, order_id = await unknown_order(monkeypatch, FakeResponse(404, {"message": "NotFound"}))

    resolved = await service.resolve_unknown(USER, order_id)

    assert await state_of(fake_db, order_id) == "failed"
    assert resolved["status"] == "failed"
    assert len(exchange.posts) == 1
    assert active_user_halts(fake_db) == []
    assert (await LiveOrderStateServiceV2(fake_db).verify_chain(order_id))["status"] == "ok"


@pytest.mark.asyncio
async def test_resolve_keeps_the_halt_when_the_lookup_fails(monkeypatch):
    exchange, fake_db, service, order_id = await unknown_order(monkeypatch, FakeResponse(503, {"message": "unavailable"}))

    resolved = await service.resolve_unknown(USER, order_id)

    assert await state_of(fake_db, order_id) == "submit_unknown"
    assert resolved["status"] == "submit_unknown"
    assert resolved["success"] is False
    assert len(exchange.posts) == 1
    assert len(active_user_halts(fake_db)) == 1


@pytest.mark.asyncio
async def test_resolve_refuses_another_users_order(monkeypatch):
    exchange, fake_db, service, order_id = await unknown_order(monkeypatch, FakeResponse(404, {"message": "NotFound"}))

    resolved = await service.resolve_unknown("someone-else", order_id)

    assert resolved["success"] is False
    assert resolved["status"] == "not_found"
    assert exchange.gets == []
    assert await state_of(fake_db, order_id) == "submit_unknown"
