import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__, warmup
from app.api import (
    ask,
    bench,
    checks,
    devices,
    explain,
    handoff,
    health,
    insights,
    notes,
    queue,
    residents,
    sim,
    staff,
    stats,
)
from app.api import replay as replay_api
from app.config import get_settings
from app.db import snowflake_rest, tiger
from app.db import snowflake_sql as sf
from app.deep import assess, forecast, sync
from app.devices import ingest
from app.scoring import evaluator
from app.sim import live_loop, replay
from app.ws import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
# The connector probes cloud metadata (IMDS) for its region on every login and logs a multi-line warning when there
# is none (Docker, Vultr): noise in `docker compose logs`, never actionable here.
logging.getLogger("snowflake.connector._core").setLevel(logging.ERROR)

SHUTDOWN_TIMEOUT_S = 5.0


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    sf.abort_detached = True  # a query orphaned by a crash is aborted by Snowflake, not left running
    tasks = [asyncio.create_task(hub.heartbeat_loop(get_settings().heartbeat_interval_s))]
    if (live_task := live_loop.start()) is not None:
        tasks.append(live_task)
    if (eval_task := evaluator.start()) is not None:
        tasks.append(eval_task)
    if (sync_task := sync.start()) is not None:  # Tiger → Snowflake + anomaly detection (deep tier)
        tasks.append(sync_task)
    if (assess_task := assess.start()) is not None:  # Cortex AI assessments on tier rise / stale top 5
        tasks.append(assess_task)
    if (forecast_task := forecast.start()) is not None:  # Snowflake FORECAST on HIGH/CRITICAL → P_trend
        tasks.append(forecast_task)
    if (ingest_task := ingest.start()) is not None:  # Polar band + iPhone bed sensor → Tiger every 1 s
        tasks.append(ingest_task)
    if get_settings().tiger_configured and get_settings().stats_flush_enabled:  # → Tiger snowflake_calls / 30 s
        tasks.append(asyncio.create_task(sync.flush_loop(), name="stats-flush"))
    if (replay_task := replay.start()) is not None:  # last night through the live pipeline (idle until started)
        tasks.append(replay_task)
    if (warm_task := warmup.start()) is not None:  # slow caches filled in the background (demo hardening)
        tasks.append(warm_task)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        # Worker threads keep blocking on their Snowflake queries after their tasks are cancelled, and the queries
        # would run on in Snowflake after this process exits: cancel them server-side, so a `--reload` or restart
        # never leaves DETECT_ANOMALIES / FORECAST runs stacking up on the warehouse.
        await cancel_snowflake()
        done, stuck = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT_S)
        for task in done:  # a background task that crashed earlier: surface it, don't drop it
            if not task.cancelled() and (exc := task.exception()) is not None:
                log.error("shutdown: task %s had failed", task.get_name(), exc_info=exc)
        for task in stuck:  # never let a wedged task block shutdown (and with it `--reload`)
            log.warning("shutdown: task %s did not stop within %.0f s", task.get_name(), SHUTDOWN_TIMEOUT_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.to_thread(sf.close_all), SHUTDOWN_TIMEOUT_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(snowflake_rest.client().aclose(), SHUTDOWN_TIMEOUT_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(tiger.close_pool(), SHUTDOWN_TIMEOUT_S)


async def cancel_snowflake() -> None:
    if not get_settings().snowflake_configured:
        return
    sessions = sf.open_session_ids()
    try:
        out = await snowflake_rest.client().cancel_running(sessions, timeout=SHUTDOWN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — best effort on the way out
        log.warning("shutdown: could not cancel Snowflake queries: %s", exc)
        return
    if out.get("sessions") or out.get("statements"):
        log.info("shutdown: cancelled running Snowflake queries %s", out)


app = FastAPI(title="Vigil", version=__version__, lifespan=lifespan)
app.include_router(health.router)
app.include_router(queue.router)
app.include_router(residents.router)
app.include_router(checks.router)
app.include_router(notes.router)
app.include_router(staff.router)
app.include_router(sim.router)
app.include_router(insights.router)
app.include_router(explain.router)
app.include_router(stats.router)
app.include_router(bench.router)
app.include_router(replay_api.router)
app.include_router(devices.router)
app.include_router(handoff.router)
app.include_router(ask.router)
app.include_router(hub.router)
app.include_router(ingest.router)
