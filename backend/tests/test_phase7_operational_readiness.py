import pytest
from pymongo.errors import OperationFailure

from services.mongo_indexes_v2 import MongoIndexServiceV2
from services.operational_readiness_v2 import OperationalReadinessServiceV2
from services.settings_v2 import SettingsV2


class FakeCollection:
    def __init__(self):
        self.indexes = []

    async def create_index(self, keys, **kwargs):
        name = kwargs.get("name") or str(keys)
        self.indexes.append({"keys": keys, "kwargs": kwargs, "name": name})
        return name

    async def drop_index(self, name):
        raise OperationFailure(f"index not found with name [{name}]", code=27)


class FakeDB:
    name = "fake_db"

    def __init__(self, ping_ok=True):
        self.ping_ok = ping_ok
        for name in [
            "market_candles",
            "trades_v2",
            "positions_v2",
            "portfolio_state",
            "ledger_entries",
            "reconciliation_reports",
            "live_readonly_reports",
            "live_order_audits",
            "execution_locks",
            "alerts",
        ]:
            setattr(self, name, FakeCollection())

    def __getattr__(self, name):
        # Services gained collections after this fake was written; create them on demand.
        if name.startswith("_"):
            raise AttributeError(name)
        collection = FakeCollection()
        setattr(self, name, collection)
        return collection

    async def command(self, command):
        if not self.ping_ok:
            raise RuntimeError("database down")
        return {"ok": 1}


def test_operational_readiness_blocks_missing_critical_env(monkeypatch):
    for key in [
        "JWT_SECRET",
        "MONGO_URL",
        "DB_NAME",
        "TRADING_MODE",
        "LIVE_TRADING_ENABLED",
        "LIVE_EXECUTION_ADAPTER",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("DEBUG", "false")

    # SettingsV2 validation refuses to load this environment at all, so build the
    # unvalidated settings a misconfigured deployment would carry.
    misconfigured = SettingsV2.model_construct(debug=False, jwt_secret="", mongo_url="", db_name="", cors_origins=["*"])
    result = OperationalReadinessServiceV2().validate_environment(strict=True, settings=misconfigured)

    assert result["status"] == "blocked"
    failed_names = {check["name"] for check in result["checks"] if not check["passed"]}
    assert "jwt_secret_configured" in failed_names
    assert "mongo_url_configured" in failed_names
    assert "db_name_configured" in failed_names
    assert "cors_origins_explicit" in failed_names


def test_operational_readiness_paper_mode_ready(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("DB_NAME", "trading_bot_test")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("TRADING_MODE", "paper")
    # live: strict readiness warns when the source is not live
    monkeypatch.setenv("MARKET_DATA_SOURCE", "live")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "disabled")

    result = OperationalReadinessServiceV2().validate_environment(strict=True, settings=SettingsV2.from_env())

    assert result["status"] == "ready"
    assert result["summary"]["failed_critical"] == 0
    assert any(check["name"] == "live_trading_disabled_by_default" and check["passed"] for check in result["checks"])


def test_operational_readiness_live_readonly_requires_credentials(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("DB_NAME", "trading_bot_test")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("TRADING_MODE", "live-readonly")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    for key in ["COINBASE_EXCHANGE_API_KEY", "COINBASE_EXCHANGE_API_SECRET", "COINBASE_EXCHANGE_PASSPHRASE"]:
        monkeypatch.delenv(key, raising=False)

    result = OperationalReadinessServiceV2().validate_environment(strict=True, settings=SettingsV2.from_env())

    assert result["status"] == "blocked"
    failed_names = {check["name"] for check in result["checks"] if not check["passed"]}
    assert "coinbase_readonly_key" in failed_names
    assert "coinbase_readonly_secret" in failed_names
    assert "coinbase_readonly_passphrase" in failed_names


def test_operational_readiness_live_trading_requires_gates(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
    monkeypatch.setenv("DB_NAME", "trading_bot_test")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("TRADING_MODE", "live-trading")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_EXECUTION_ADAPTER", "coinbase_exchange_v2")
    monkeypatch.setenv("LIVE_APPROVAL_TOKEN", "approve")
    monkeypatch.setenv("LIVE_MAX_ORDER_NOTIONAL_USD", "25")
    monkeypatch.setenv("COINBASE_EXCHANGE_API_KEY", "key")
    monkeypatch.setenv("COINBASE_EXCHANGE_API_SECRET", "secret")
    monkeypatch.setenv("COINBASE_EXCHANGE_PASSPHRASE", "pass")

    result = OperationalReadinessServiceV2().validate_environment(strict=True, settings=SettingsV2.from_env())

    assert result["status"] == "ready"
    assert result["summary"]["failed_critical"] == 0


@pytest.mark.asyncio
async def test_database_health_reports_ready_and_blocked():
    service = OperationalReadinessServiceV2()

    ready = await service.database_health(FakeDB(ping_ok=True))
    blocked = await service.database_health(FakeDB(ping_ok=False))

    assert ready["status"] == "ready"
    assert blocked["status"] == "blocked"


@pytest.mark.asyncio
async def test_mongo_index_service_creates_expected_indexes():
    db = FakeDB()
    result = await MongoIndexServiceV2(db).ensure_indexes()

    assert result["status"] == "ok"
    assert "uniq_market_candle" in result["created_or_verified"]
    assert "uniq_ledger_entry" in result["created_or_verified"]
    assert "live_audits_user_created" in result["created_or_verified"]
