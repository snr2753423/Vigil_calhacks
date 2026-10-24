import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import checks, health, queue, residents, sim
from app.config import get_settings
from app.db import tiger
from app.scoring import evaluator
from app.sim import live_loop
from app.ws import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

SHUTDOWN_TIMEOUT_S = 5.0


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    tasks = [asyncio.create_task(hub.heartbeat_loop(get_settings().heartbeat_interval_s))]
    if (live_task := live_loop.start()) is not None:
        tasks.append(live_task)
    if (eval_task := evaluator.start()) is not None:
        tasks.append(eval_task)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        done, stuck = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT_S)
        for task in done:  # a background task that crashed earlier: surface it, don't drop it
            if not task.cancelled() and (exc := task.exception()) is not None:
                log.error("shutdown: task %s had failed", task.get_name(), exc_info=exc)
        for task in stuck:  # never let a wedged task block shutdown (and with it `--reload`)
            log.warning("shutdown: task %s did not stop within %.0f s", task.get_name(), SHUTDOWN_TIMEOUT_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(tiger.close_pool(), SHUTDOWN_TIMEOUT_S)


app = FastAPI(title="Vigil", version=__version__, lifespan=lifespan)
app.include_router(health.router)
app.include_router(queue.router)
app.include_router(residents.router)
app.include_router(checks.router)
app.include_router(sim.router)
app.include_router(hub.router)
