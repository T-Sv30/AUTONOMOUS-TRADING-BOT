import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi.security import HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext

from runtime_config import API_EMBED_BOT_MANAGER, RUN_MONGO_INDEX_BOOTSTRAP, RUNTIME_ROLE
from services.bot_manager import BotManager
from services.mongo_indexes_v2 import MongoIndexServiceV2
from services.structured_logging import configure_logging, log_event

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

configure_logging()
logger = logging.getLogger(__name__)

mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
db_name = os.environ.get("DB_NAME", "trading_bot")
client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
bot_manager = None
manager_task = None


async def ensure_mongo_indexes() -> dict:
    index_result = await MongoIndexServiceV2(db).ensure_indexes()
    if index_result.get("errors"):
        # A failed index (e.g. a unique index over existing duplicates) must not stop startup; ops cleans up and reruns.
        log_event(logger, logging.WARNING, "mongo_indexes_degraded", result=index_result)
    else:
        log_event(logger, logging.INFO, "mongo_indexes_ready", result=index_result)
    return index_result


async def start_embedded_bot_manager() -> asyncio.Task | None:
    global bot_manager
    if not API_EMBED_BOT_MANAGER:
        log_event(logger, logging.INFO, "bot_manager_not_embedded", runtime_role=RUNTIME_ROLE.value)
        return None
    bot_manager = BotManager(db)
    task = asyncio.create_task(bot_manager.start_manager())
    log_event(logger, logging.INFO, "bot_manager_started", embedded=True, runtime_role=RUNTIME_ROLE.value)
    return task


async def stop_embedded_bot_manager(task: asyncio.Task | None) -> None:
    global bot_manager
    if not bot_manager:
        return
    await bot_manager.stop_manager()
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    bot_manager = None


@asynccontextmanager
async def lifespan(app):
    global manager_task
    log_event(logger, logging.INFO, "app_starting", service="autonomous_trading_bot", runtime_role=RUNTIME_ROLE.value)

    if RUN_MONGO_INDEX_BOOTSTRAP:
        try:
            await ensure_mongo_indexes()
        except Exception as exc:
            log_event(logger, logging.ERROR, "mongo_index_setup_failed", error=str(exc))
            raise
    else:
        log_event(logger, logging.INFO, "mongo_index_bootstrap_skipped", runtime_role=RUNTIME_ROLE.value)

    manager_task = await start_embedded_bot_manager()

    yield

    log_event(logger, logging.INFO, "app_shutting_down", runtime_role=RUNTIME_ROLE.value)
    await stop_embedded_bot_manager(manager_task)
    client.close()
    log_event(logger, logging.INFO, "app_shutdown_complete", runtime_role=RUNTIME_ROLE.value)
