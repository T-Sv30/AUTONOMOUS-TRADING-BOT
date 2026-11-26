"""Shared pytest configuration.

app_state creates one Motor client at import time. Motor binds to the event
loop that first uses it, so every async test must share one session-scoped
loop; otherwise later tests fail with "Event loop is closed".
"""

import pytest
from pytest_asyncio import is_async_test


def pytest_collection_modifyitems(items):
    session_loop = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if is_async_test(item):
            item.add_marker(session_loop, append=False)
