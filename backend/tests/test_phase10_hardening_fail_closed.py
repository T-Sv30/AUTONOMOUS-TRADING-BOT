from services.secret_hardening_service_v2 import SecretHardeningServiceV2
from services.settings_v2 import SettingsV2, TradingMode


def make_settings(**overrides):
    base = {
        "debug": False,
        "market_data_source": "simulated",
        "trading_mode": TradingMode.PAPER,
        "jwt_secret": "j" * 40,
        "cors_origins": ["https://example.com"],
        "live_trading_enabled": False,
        "live_execution_adapter": "disabled",
        "live_max_order_notional_usd": 25.0,
        "live_manual_approval_required": True,
        "live_approval_token": "",
        "coinbase_live_order_kill_switch": True,
    }
    base.update(overrides)
    return SettingsV2(**base)


def names(report):
    return {item["name"] for item in report["blockers"]}


def test_live_readonly_mode_is_not_hardened_without_exchange_configuration():
    report = SecretHardeningServiceV2.evaluate(settings=make_settings(trading_mode=TradingMode.LIVE_READONLY), env={})

    assert report["ready_for_live_credentials"] is False
    assert "coinbase_credentials_present_when_live_mode" in names(report)


def test_open_submit_switch_is_blocked_outside_live_trading_mode():
    report = SecretHardeningServiceV2.evaluate(settings=make_settings(coinbase_live_order_kill_switch=False), env={})

    assert report["ready_for_live_credentials"] is False
    assert "kill_switch_defaults_safe_when_not_live_trading" in names(report)


def test_large_manual_order_cap_is_blocked_before_expansion():
    report = SecretHardeningServiceV2.evaluate(settings=make_settings(live_max_order_notional_usd=100.0), env={})

    assert report["ready_for_live_credentials"] is False
    assert "live_order_notional_cap_is_tiny" in names(report)
