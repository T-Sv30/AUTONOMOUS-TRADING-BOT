import asyncio

import pytest

from services.bot_manager import BotManager


class FakeHeartbeat:
    async def release_bot_ownership(self, user_id):
        pass


class RecordingEngine:
    def __init__(self, events):
        self.events = events

    async def stop(self):
        self.events.append("engine_stop")


@pytest.mark.asyncio
async def test_stop_bot_cancels_the_cycle_before_stopping_the_engine():
    events = []
    manager = BotManager(db=None, heartbeat=FakeHeartbeat())

    async def cycle():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            events.append("task_cancelled")
            raise

    manager.active_bots["u1"] = asyncio.create_task(cycle())
    manager.bot_engines["u1"] = RecordingEngine(events)
    await asyncio.sleep(0)

    await manager.stop_bot("u1")

    assert events == ["task_cancelled", "engine_stop"]
    assert "u1" not in manager.active_bots
    assert "u1" not in manager.bot_engines
