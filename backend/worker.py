import asyncio
import logging
import signal

from app_state import client, db
from runtime_config import RUNTIME_ROLE, RuntimeRole
from services.bot_manager import BotManager
from services.structured_logging import configure_logging, log_event

configure_logging()
logger = logging.getLogger(__name__)


async def run_worker() -> None:
    """Run the autonomous paper bot worker as a dedicated process.

    The worker owns BotManager polling and paper bot task lifecycle. It does not
    expose HTTP and it does not enable autonomous live trading.
    """
    if RUNTIME_ROLE not in {RuntimeRole.WORKER, RuntimeRole.ALL}:
        log_event(logger, logging.WARNING, "worker_started_with_non_worker_role", runtime_role=RUNTIME_ROLE.value)

    manager = BotManager(db)
    stop_event = asyncio.Event()

    def request_stop(signum=None):
        log_event(logger, logging.INFO, "worker_stop_requested", signal=signum)
        manager.running = False
        stop_event.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signame, None)
        if signum is None:
            continue
        try:
            loop.add_signal_handler(signum, request_stop, signame)
        except NotImplementedError:
            signal.signal(signum, lambda *_, name=signame: request_stop(name))

    log_event(logger, logging.INFO, "worker_starting", runtime_role=RUNTIME_ROLE.value)
    manager_task = asyncio.create_task(manager.start_manager(), name="bot-manager")
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        request_stop("cancelled")
        raise
    finally:
        manager.running = False
        manager_task.cancel()
        try:
            await manager_task
        except asyncio.CancelledError:
            pass
        await manager.stop_manager()
        client.close()
        log_event(logger, logging.INFO, "worker_stopped", runtime_role=RUNTIME_ROLE.value)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
