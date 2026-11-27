from datetime import UTC, datetime

import pytest
from fakes import FakeDB

from auth_core import UserCreate, UserLogin
from services.coinbase_live_execution_adapter_v2 import CoinbaseLiveExecutionAdapterV2, CoinbaseLiveExecutionError
from services.execution_service_v2 import ExecutionServiceV2
from services.live_approval_challenge_service_v2 import LiveApprovalChallengeServiceV2
from services.live_order_audit_service_v2 import LiveOrderAuditServiceV2
from services.live_trading_gate_v2 import LiveTradingGateConfig, LiveTradingGateV2
from services.live_trading_service_v2 import LiveTradingServiceV2
from services.trading_mode_v2 import TradingModeError, TradingModeService
from services.trading_service_v2 import TradingServiceV2


class FakeLiveAdapter:
    async def place_market_buy(self, symbol, notional_usd, client_order_id=None, dry_run=False):
        return {
            "success": True,
            "status": "dry_run" if dry_run else "submitted",
            "symbol": symbol,
            "side": "BUY",
            "client_order_id": client_order_id,
            "notional_usd": notional_usd,
            "live_execution": not dry_run,
        }

    async def place_market_sell(self, symbol, base_units, client_order_id=None, dry_run=False):
        return {
            "success": True,
            "status": "dry_run" if dry_run else "submitted",
            "symbol": symbol,
            "side": "SELL",
            "client_order_id": client_order_id,
            "base_units": base_units,
            "live_execution": not dry_run,
        }


def gate_config(**overrides):
    values = {
        "live_trading_enabled": False,
        "execution_adapter": "disabled",
        "max_order_notional_usd": 25.0,
        "allowed_symbols": ("BTC-USD", "ETH-USD"),
        "manual_approval_required": True,
        "signed_approval_required": False,
        "approval_token": "approve-me",
    }
    values.update(overrides)
    return LiveTradingGateConfig(**values)


def test_live_trading_gate_fails_closed_by_default():
    gate = LiveTradingGateV2(gate_config())
    result = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token="approve-me",
        dry_run=False,
    )

    assert result["allowed"] is False
    assert "LIVE_TRADING_ENABLED" in result["reason"]


def test_live_trading_gate_allows_dry_run_without_approval_when_all_hard_gates_pass():
    gate = LiveTradingGateV2(gate_config(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2"))
    result = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token=None,
        dry_run=True,
    )

    assert result["allowed"] is True
    assert result["dry_run"] is True


def test_live_trading_gate_requires_approval_for_actual_order():
    gate = LiveTradingGateV2(gate_config(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2"))
    blocked = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token="wrong",
        dry_run=False,
    )
    allowed = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token="approve-me",
        dry_run=False,
    )

    assert blocked["allowed"] is False
    assert "approval" in blocked["reason"]
    assert allowed["allowed"] is True


def test_live_trading_gate_signed_approval_mode_requires_token_presence():
    gate = LiveTradingGateV2(gate_config(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2", signed_approval_required=True))
    blocked = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token=None,
        dry_run=False,
    )
    token_present = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token="challenge.signature",
        dry_run=False,
    )

    assert blocked["allowed"] is False
    assert "signed live approval" in blocked["reason"]
    assert token_present["allowed"] is True


def test_live_trading_gate_rejects_excess_notional_and_unknown_symbol():
    gate = LiveTradingGateV2(gate_config(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2", max_order_notional_usd=25.0))
    too_large = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="BTC-USD",
        side="BUY",
        notional_usd=26.0,
        approval_token="approve-me",
        dry_run=False,
    )
    bad_symbol = gate.safe_preflight(
        trading_mode="live-trading",
        user_config={"live_trading_enabled": True},
        symbol="DOGE-USD",
        side="BUY",
        notional_usd=10.0,
        approval_token="approve-me",
        dry_run=False,
    )

    assert too_large["allowed"] is False
    assert "exceeds" in too_large["reason"]
    assert bad_symbol["allowed"] is False
    assert "LIVE_ALLOWED_SYMBOLS" in bad_symbol["reason"]


def test_coinbase_live_adapter_payloads_and_dry_run_preview():
    buy_payload = CoinbaseLiveExecutionAdapterV2.market_buy_payload("BTC-USD", 12.345, client_order_id="client-1")
    sell_payload = CoinbaseLiveExecutionAdapterV2.market_sell_payload("ETH-USD", 0.1234567891234, client_order_id="client-2")

    assert buy_payload == {"type": "market", "side": "buy", "product_id": "BTC-USD", "funds": "12.35", "client_oid": "client-1"}
    assert sell_payload == {"type": "market", "side": "sell", "product_id": "ETH-USD", "size": "0.123456789123", "client_oid": "client-2"}


def test_coinbase_live_adapter_kill_switch_blocks_non_dry_run(monkeypatch):
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", "true")
    with pytest.raises(CoinbaseLiveExecutionError):
        CoinbaseLiveExecutionAdapterV2.assert_live_orders_not_killed()


def refusing_session_factory(*args, **kwargs):
    pytest.fail("the kill switch must stop a live POST before any HTTP session is created")


def live_adapter_with_credentials(session_factory):
    return CoinbaseLiveExecutionAdapterV2(api_key="key", api_secret="c2VjcmV0", passphrase="pass", session_factory=session_factory)


@pytest.mark.asyncio
async def test_kill_switch_unset_blocks_live_post(monkeypatch):
    monkeypatch.delenv("COINBASE_LIVE_ORDER_KILL_SWITCH", raising=False)
    adapter = live_adapter_with_credentials(refusing_session_factory)

    with pytest.raises(CoinbaseLiveExecutionError, match="COINBASE_LIVE_ORDER_KILL_SWITCH"):
        await adapter._post_private("/orders", {"type": "market", "side": "buy", "product_id": "BTC-USD", "funds": "5"})


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "  ", "maybe", "disabled", "2"])
async def test_kill_switch_empty_or_malformed_blocks_live_post(monkeypatch, value):
    monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", value)
    adapter = live_adapter_with_credentials(refusing_session_factory)

    with pytest.raises(CoinbaseLiveExecutionError, match="COINBASE_LIVE_ORDER_KILL_SWITCH"):
        await adapter._post_private("/orders", {"type": "market", "side": "buy", "product_id": "BTC-USD", "funds": "5"})


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("maybe", True),
        ("true", True),
        ("1", True),
        ("0", False),
        ("false", False),
        ("No", False),
        (" off ", False),
    ],
)
def test_adapter_and_settings_parse_the_kill_switch_the_same_way(monkeypatch, value, expected):
    from services.settings_v2 import SettingsV2

    if value is None:
        monkeypatch.delenv("COINBASE_LIVE_ORDER_KILL_SWITCH", raising=False)
    else:
        monkeypatch.setenv("COINBASE_LIVE_ORDER_KILL_SWITCH", value)

    assert CoinbaseLiveExecutionAdapterV2.live_order_kill_switch_enabled() is expected
    assert SettingsV2.from_env().coinbase_live_order_kill_switch is expected


@pytest.mark.asyncio
async def test_live_trading_service_blocks_before_adapter_when_gate_fails(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    db = FakeDB()
    await db.bot_configs.insert_one({"user_id": "user-1", "live_trading_enabled": True})
    service = LiveTradingServiceV2(db, adapter=FakeLiveAdapter(), gate=LiveTradingGateV2(gate_config()))
    # The service verifies a signed challenge before the gate, so the gate is what blocks here.
    challenge = await LiveApprovalChallengeServiceV2(db).create_challenge(user_id="user-1", side="BUY", symbol="BTC-USD", notional_usd=10.0)

    result = await service.place_market_buy("user-1", "BTC-USD", 10.0, approval_token=challenge["approval_token"], dry_run=False)

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert len(db.live_order_audits.docs) == 1
    assert db.live_order_audits.docs[0]["status"] == "blocked"
    assert db.live_order_audits.docs[0]["previous_hash"] == "GENESIS"
    assert db.live_order_audits.docs[0]["audit_hash"]


@pytest.mark.asyncio
async def test_live_trading_service_allows_dry_run_preview_and_records_audit(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    db = FakeDB()
    await db.bot_configs.insert_one({"user_id": "user-1", "live_trading_enabled": True})
    gate = LiveTradingGateV2(gate_config(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2"))
    service = LiveTradingServiceV2(db, adapter=FakeLiveAdapter(), gate=gate)

    result = await service.place_market_buy("user-1", "BTC-USD", 10.0, dry_run=True)

    assert result["success"] is True
    assert result["status"] == "dry_run"
    assert result["gate"]["allowed"] is True
    assert len(db.live_order_audits.docs) == 2
    assert db.live_order_audits.docs[0]["status"] == "preflight_passed"
    assert db.live_order_audits.docs[1]["status"] == "dry_run"
    assert db.live_order_audits.docs[1]["previous_hash"] == db.live_order_audits.docs[0]["audit_hash"]


@pytest.mark.asyncio
async def test_live_order_audit_chain_detects_tampering():
    db = FakeDB()
    audits = LiveOrderAuditServiceV2(db)
    await audits.append({"user_id": "user-1", "status": "preflight_passed", "symbol": "BTC-USD"})
    await audits.append({"user_id": "user-1", "status": "dry_run", "symbol": "BTC-USD"})

    ok = await audits.verify_user_chain("user-1")
    db.live_order_audits.docs[0]["status"] = "tampered"
    tampered = await audits.verify_user_chain("user-1")

    assert ok["status"] == "ok"
    assert tampered["status"] == "tamper_detected"


@pytest.mark.asyncio
async def test_live_trading_service_actual_order_requires_approval(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    db = FakeDB()
    await db.bot_configs.insert_one({"user_id": "user-1", "live_trading_enabled": True})
    # Pre-submit safety (added after this test) needs a fresh live-readonly reconciliation.
    await db.live_readonly_reports.insert_one({"user_id": "user-1", "checked_at": datetime.now(UTC).isoformat(), "status": "ok"})
    gate = LiveTradingGateV2(gate_config(live_trading_enabled=True, execution_adapter="coinbase_exchange_v2", signed_approval_required=True))
    service = LiveTradingServiceV2(db, adapter=FakeLiveAdapter(), gate=gate)
    challenge = await LiveApprovalChallengeServiceV2(db).create_challenge(user_id="user-1", side="BUY", symbol="BTC-USD", notional_usd=10.0)

    blocked = await service.place_market_buy("user-1", "BTC-USD", 10.0, approval_token="wrong", dry_run=False)
    allowed = await service.place_market_buy("user-1", "BTC-USD", 10.0, approval_token=challenge["approval_token"], dry_run=False)

    assert blocked["success"] is False
    assert allowed["success"] is True, allowed
    assert allowed["status"] == "submitted"


def test_trading_mode_live_trading_still_forces_gated_service(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "coinbase_exchange_v2")
    mode = TradingModeService()
    description = mode.describe()

    assert description["gated_live_execution_available"] is True
    assert description["live_order_methods_available"] is True
    assert description["live_order_entrypoint"] == "LiveTradingServiceV2"
    assert description["live_execution_enabled"] is False
    with pytest.raises(TradingModeError):
        mode.assert_can_trade()


def test_autonomous_execution_service_is_paper_only():
    execution = ExecutionServiceV2()
    assert isinstance(execution.trading_service, TradingServiceV2)
    assert execution.trading_service.__class__.__name__ == "TradingServiceV2"


def test_auth_models_reject_invalid_email_and_short_signup_password():
    with pytest.raises(ValueError):
        UserCreate(email="not-an-email", password="long-enough-password")
    with pytest.raises(ValueError):
        UserCreate(email="user@example.com", password="short")
    assert UserLogin(email="user@example.com", password="x")
