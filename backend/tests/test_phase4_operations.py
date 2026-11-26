import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.alert_service import AlertService
from services.structured_logging import JsonFormatter

BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_backend_python(code: str, env_updates: dict[str, str | None]):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)
    for key, value in env_updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    def find(self, query, projection=None):
        matches = [dict(doc) for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        return FakeCursor(matches)


class FakeDB:
    def __init__(self):
        self.alerts = FakeCollection()


def test_runtime_config_rejects_missing_auth_secret_in_non_debug():
    result = run_backend_python(
        "import runtime_config",
        {"DEBUG": "False", "JWT_SECRET": None, "CORS_ORIGINS": "http://localhost:3000"},
    )
    assert result.returncode != 0
    assert "JWT_SECRET must be configured" in result.stderr


def test_json_formatter_outputs_machine_readable_log_record():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"event": "unit_test", "user_id": "u1"}
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["event"] == "unit_test"
    assert payload["user_id"] == "u1"


@pytest.mark.asyncio
async def test_alert_service_persists_and_lists_alerts():
    db = FakeDB()
    service = AlertService(db)
    await service.emit("user-1", "bot_halted", "critical", "halted", {"reason": "risk"})
    alerts = await service.list_alerts("user-1")
    assert len(alerts) == 1
    assert alerts[0]["type"] == "bot_halted"
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["acknowledged"] is False
