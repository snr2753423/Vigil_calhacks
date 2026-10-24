import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.config import get_settings
from app.sim import live_loop
from app.ws import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    tasks = [asyncio.create_task(hub.heartbeat_loop(get_settings().heartbeat_interval_s))]
    if (live_task := live_loop.start()) is not None:
        tasks.append(live_task)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Vigil", version=__version__, lifespan=lifespan)
app.include_router(health.router)
app.include_router(hub.router)
