import logging

import pytest
from pydantic import ValidationError

from services.settings_v2 import RuntimeRole, SettingsV2, TradingMode


def test_settings_redacted_report_never_exposes_secret_values():
    settings = SettingsV2(
        debug=False,
        jwt_secret="x" * 40,
        cors_origins=["https://example.com"],
        coinbase_exchange_api_key="key-value",
        coinbase_exchange_api_secret="secret-value",
        coinbase_exchange_passphrase="passphrase-value",
        live_approval_token="approval-value",
    )

    report = settings.redacted_report()
    rendered = str(report)

    assert "key-value" not in rendered
    assert "secret-value" not in rendered
    assert "passphrase-value" not in rendered
    assert "approval-value" not in rendered
    assert report["coinbase"]["api_key"]["configured"] is True
    assert report["coinbase"]["api_secret"]["redacted"] == "***"
    assert report["live_trading"]["approval_token"]["redacted"] == "***"


def test_settings_rejects_weak_production_jwt_secret():
    with pytest.raises(ValidationError):
        SettingsV2(debug=False, jwt_secret="short", cors_origins=["https://example.com"])


def test_settings_rejects_wildcard_cors_in_production():
    with pytest.raises(ValidationError):
        SettingsV2(debug=False, jwt_secret="x" * 40, cors_origins=["*"])


def test_settings_accepts_debug_wildcard_cors():
    settings = SettingsV2(debug=True, jwt_secret="local-debug-placeholder", cors_origins=["*"])
    assert settings.cors_origins == ["*"]


def test_settings_rejects_live_enabled_outside_live_trading_mode():
    with pytest.raises(ValidationError):
        SettingsV2(
            debug=False,
            jwt_secret="x" * 40,
            cors_origins=["https://example.com"],
            trading_mode=TradingMode.PAPER,
            live_trading_enabled=True,
        )


def test_settings_requires_live_adapter_and_approval_for_live_trading():
    with pytest.raises(ValidationError):
        SettingsV2(
            debug=False,
            jwt_secret="x" * 40,
            cors_origins=["https://example.com"],
            trading_mode=TradingMode.LIVE_TRADING,
            live_trading_enabled=True,
            live_execution_adapter="disabled",
            live_manual_approval_required=True,
            live_approval_token="approval-token",
        )


def test_settings_accepts_valid_live_trading_configuration():
    settings = SettingsV2(
        debug=False,
        jwt_secret="x" * 40,
        cors_origins=["https://example.com"],
        trading_mode=TradingMode.LIVE_TRADING,
        live_trading_enabled=True,
        live_execution_adapter="coinbase_exchange_v2",
        live_manual_approval_required=True,
        live_approval_token="approval-token",
    )
    assert settings.trading_mode == TradingMode.LIVE_TRADING
    assert settings.live_trading_enabled is True


def test_settings_runtime_role_values():
    settings = SettingsV2(debug=True, runtime_role=RuntimeRole.WORKER, jwt_secret="debug", cors_origins=["http://localhost:3000"])
    assert settings.runtime_role == RuntimeRole.WORKER


@pytest.fixture
def valid_env(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)


def test_market_data_source_defaults_to_live(monkeypatch, valid_env):
    monkeypatch.delenv("MARKET_DATA_SOURCE", raising=False)
    monkeypatch.delenv("SIMULATION_MODE", raising=False)
    assert SettingsV2.from_env().market_data_source == "live"


def test_market_data_source_rejects_unknown_value(monkeypatch, valid_env):
    monkeypatch.setenv("MARKET_DATA_SOURCE", "sim")
    with pytest.raises(ValueError, match="live, simulated, replay"):
        SettingsV2.from_env()


@pytest.mark.parametrize("legacy,expected", [("True", "simulated"), ("false", "live")])
def test_legacy_simulation_mode_maps_with_deprecation_warning(monkeypatch, caplog, valid_env, legacy, expected):
    caplog.set_level(logging.WARNING)
    monkeypatch.delenv("MARKET_DATA_SOURCE", raising=False)
    monkeypatch.setenv("SIMULATION_MODE", legacy)
    assert SettingsV2.from_env().market_data_source == expected
    assert "deprecated" in caplog.text and "MARKET_DATA_SOURCE" in caplog.text


def test_market_data_source_wins_over_legacy(monkeypatch, valid_env):
    monkeypatch.setenv("SIMULATION_MODE", "True")
    monkeypatch.setenv("MARKET_DATA_SOURCE", "live")
    assert SettingsV2.from_env().market_data_source == "live"
