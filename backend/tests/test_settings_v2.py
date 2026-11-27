import logging

import pytest
from pydantic import ValidationError

from services import market_data_service
from services.market_data_service import MarketDataService, resolve_market_data_source
from services.paper_execution_adapter_v2 import PaperExecutionAdapterV2
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
    monkeypatch.setattr(market_data_service, "_simulation_mode_warned", False, raising=False)
    monkeypatch.delenv("MARKET_DATA_SOURCE", raising=False)
    monkeypatch.setenv("SIMULATION_MODE", legacy)
    assert SettingsV2.from_env().market_data_source == expected
    assert "deprecated" in caplog.text and "MARKET_DATA_SOURCE" in caplog.text


def test_market_data_source_wins_over_legacy(monkeypatch, valid_env):
    monkeypatch.setenv("SIMULATION_MODE", "True")
    monkeypatch.setenv("MARKET_DATA_SOURCE", "live")
    assert SettingsV2.from_env().market_data_source == "live"


def test_simulation_mode_deprecation_is_logged_once_per_process(monkeypatch, caplog):
    """M1.5b task 16: the warning is logged once, not on every resolve."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(market_data_service, "_simulation_mode_warned", False, raising=False)
    for _ in range(3):
        assert resolve_market_data_source({"SIMULATION_MODE": "true"}) == "simulated"
    assert sum("SIMULATION_MODE is deprecated" in record.getMessage() for record in caplog.records) == 1


NUMERIC_SETTINGS = [
    "MARKET_DATA_TIMEOUT_SECONDS",
    "SIM_SPREAD_BPS",
    "PAPER_EXTRA_SLIPPAGE_BPS",
    "PAPER_FALLBACK_SPREAD_BPS",
    "LIVE_ORDER_TIMEOUT_SECONDS",
    "RISK_METRICS_RETENTION_DAYS",
    "JWT_EXPIRE_HOURS",
]


@pytest.fixture
def clean_numeric_env(monkeypatch, valid_env):
    for name in NUMERIC_SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "name,value",
    [
        ("SIM_SPREAD_BPS", "abc"),
        ("SIM_SPREAD_BPS", "-1"),
        ("SIM_SPREAD_BPS", "101"),
        ("MARKET_DATA_TIMEOUT_SECONDS", "0"),
        ("MARKET_DATA_TIMEOUT_SECONDS", "121"),
        ("MARKET_DATA_TIMEOUT_SECONDS", "ten"),
        ("PAPER_EXTRA_SLIPPAGE_BPS", "101"),
        ("PAPER_EXTRA_SLIPPAGE_BPS", "abc"),
        ("PAPER_FALLBACK_SPREAD_BPS", "nan"),
        ("PAPER_FALLBACK_SPREAD_BPS", "101"),
        ("LIVE_ORDER_TIMEOUT_SECONDS", "0"),
        ("LIVE_ORDER_TIMEOUT_SECONDS", "61"),
        ("LIVE_ORDER_TIMEOUT_SECONDS", "inf"),
        ("RISK_METRICS_RETENTION_DAYS", "0"),
        ("RISK_METRICS_RETENTION_DAYS", "3651"),
        ("RISK_METRICS_RETENTION_DAYS", "abc"),
        ("JWT_EXPIRE_HOURS", "0"),
        ("JWT_EXPIRE_HOURS", "169"),
        ("JWT_EXPIRE_HOURS", "abc"),
    ],
)
def test_malformed_or_out_of_range_numeric_setting_fails_load_naming_it(monkeypatch, clean_numeric_env, name, value):
    """M1.5b task 16: a bad value fails settings load with the variable named, instead of a silent default."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        SettingsV2.from_env()


@pytest.mark.parametrize(
    "name,low,high",
    [
        ("MARKET_DATA_TIMEOUT_SECONDS", "1", "120"),
        ("SIM_SPREAD_BPS", "0", "100"),
        ("PAPER_EXTRA_SLIPPAGE_BPS", "0", "100"),
        ("PAPER_FALLBACK_SPREAD_BPS", "0", "100"),
        ("LIVE_ORDER_TIMEOUT_SECONDS", "1", "60"),
        ("RISK_METRICS_RETENTION_DAYS", "1", "3650"),
        ("JWT_EXPIRE_HOURS", "1", "168"),
    ],
)
def test_numeric_settings_accept_their_bounds(monkeypatch, clean_numeric_env, name, low, high):
    for value in (low, high, ""):
        monkeypatch.setenv(name, value)
        SettingsV2.from_env()


def test_market_data_service_reads_validated_timeout_and_spread(monkeypatch, clean_numeric_env):
    """M1.5b task 16: the market data service takes its defaults from the validated values."""
    monkeypatch.setenv("MARKET_DATA_TIMEOUT_SECONDS", "500")
    with pytest.raises(ValueError, match="MARKET_DATA_TIMEOUT_SECONDS"):
        MarketDataService(source="simulated")
    monkeypatch.setenv("MARKET_DATA_TIMEOUT_SECONDS", "30")
    service = MarketDataService(source="simulated")
    assert service.timeout_seconds == 30.0
    monkeypatch.setenv("SIM_SPREAD_BPS", "abc")
    with pytest.raises(ValueError, match="SIM_SPREAD_BPS"):
        service._simulate_price_data("BTC-USD")


@pytest.mark.parametrize("name", ["PAPER_EXTRA_SLIPPAGE_BPS", "PAPER_FALLBACK_SPREAD_BPS"])
def test_paper_adapter_rejects_a_bad_spread_setting_instead_of_defaulting(monkeypatch, clean_numeric_env, name):
    """M1.5b task 16: the paper adapter takes its defaults from the validated values."""
    monkeypatch.setenv(name, "abc")
    with pytest.raises(ValueError, match=name):
        PaperExecutionAdapterV2.from_env()
