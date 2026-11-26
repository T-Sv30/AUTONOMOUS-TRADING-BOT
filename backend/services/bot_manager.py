import asyncio
import logging

from services.bot_engine import BotEngine
from services.worker_heartbeat_service_v2 import WorkerHeartbeatServiceV2

logger = logging.getLogger(__name__)


class BotManager:
    """Manages paper bot instances with worker lifecycle and ownership renewal."""

    POLL_SECONDS = 5

    def __init__(self, db, heartbeat: WorkerHeartbeatServiceV2 | None = None):
        self.db = db
        self.active_bots: dict[str, asyncio.Task] = {}
        self.bot_engines: dict[str, BotEngine] = {}
        self.running = False
        self.stopping = False
        self.heartbeat = heartbeat or WorkerHeartbeatServiceV2(db)

    async def start_manager(self):
        """Start the bot manager and monitor active paper bot configs."""
        self.running = True
        self.stopping = False
        logger.info("Bot Manager started")
        await self.heartbeat.mark_lifecycle(role="worker", status="starting", active_bots=[])

        while self.running:
            try:
                active_user_ids = list(self.active_bots.keys())
                await self.heartbeat.renew_active_ownerships(active_user_ids)
                await self.heartbeat.beat(role="worker", status="running", active_bots=active_user_ids)

                active_configs = await self.db.bot_configs.find({"is_active": True}, {"_id": 0}).to_list(100)

                for config in active_configs:
                    user_id = config["user_id"]
                    if user_id not in self.active_bots or self.active_bots[user_id].done():
                        acquired = await self.heartbeat.acquire_bot_ownership(user_id)
                        if not acquired:
                            logger.info("Bot ownership held by another worker for user %s", user_id)
                            continue
                        logger.info("Starting bot for user %s", user_id)
                        await self.start_bot(user_id)

                for user_id in list(self.active_bots.keys()):
                    config = await self.db.bot_configs.find_one({"user_id": user_id})
                    if not config or not config.get("is_active"):
                        logger.info("Stopping bot for user %s", user_id)
                        await self.stop_bot(user_id, reason="config_inactive")

                await asyncio.sleep(self.POLL_SECONDS)

            except asyncio.CancelledError:
                logger.info("Bot manager received cancellation")
                break
            except Exception as exc:
                logger.error("Bot manager error: %s", exc)
                await self.heartbeat.beat(role="worker", status="error", active_bots=list(self.active_bots.keys()), metadata={"error": str(exc)})
                await asyncio.sleep(self.POLL_SECONDS)

        await self.stop_manager()

    async def start_bot(self, user_id: str):
        """Start a paper bot for a specific user."""
        if user_id in self.active_bots and not self.active_bots[user_id].done():
            logger.info("Bot already running for user %s", user_id)
            return

        bot_engine = BotEngine(self.db)
        self.bot_engines[user_id] = bot_engine
        task = asyncio.create_task(bot_engine.start(user_id), name=f"paper-bot-{user_id}")
        self.active_bots[user_id] = task
        logger.info("Bot started for user %s", user_id)

    async def stop_bot(self, user_id: str, reason: str = "operator_stop"):
        """Stop a paper bot for a specific user and release ownership."""
        # Cancel the running cycle first so it cannot open a new market data session after the engine stops.
        task = self.active_bots.get(user_id)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            del self.active_bots[user_id]

        if user_id in self.bot_engines:
            await self.bot_engines[user_id].stop()
            del self.bot_engines[user_id]

        await self.heartbeat.release_bot_ownership(user_id)
        logger.info("Bot stopped for user %s: %s", user_id, reason)

    async def stop_manager(self):
        """Stop the bot manager and all running paper bots."""
        if self.stopping:
            return
        self.stopping = True
        self.running = False
        await self.heartbeat.mark_lifecycle(role="worker", status="stopping", active_bots=list(self.active_bots.keys()))

        for user_id in list(self.active_bots.keys()):
            await self.stop_bot(user_id, reason="manager_shutdown")

        released = await self.heartbeat.release_all_owned_bots()
        await self.heartbeat.mark_stopped(role="worker", metadata={"released_ownerships": released})
        logger.info("Bot Manager stopped")
