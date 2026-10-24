"""Startup warm-up (demo hardening): fill the slow caches in the background right after the backend starts, so the
first visit to each page is as fast as the second. Nothing waits on it, and a step that fails (Snowflake down, no
prepared night yet) is logged and skipped: the page then fills that cache itself on first use, as before.

Steps: the replay night's outcome (fixed alarms over ~1 M raw readings, ~2 s), the Snowflake inventory for Under the
hood, the recorded night's stored handoff, and the Analyst semantic-model upload that the first Ask otherwise pays.
`/api/health` → `warmup` shows each step's time or error.
"""

import asyncio
import logging
import time
from datetime import date

from app.api.deps import facility_tz
from app.config import get_settings
from app.db import tiger

log = logging.getLogger(__name__)

START_DELAY_S = 2.0  # let the live loop and evaluator take their first Tiger connections first
STEP_TIMEOUT_S = 60.0

status: dict = {"phase": "idle", "steps": {}}


async def _step(name: str, coro) -> None:
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(coro, STEP_TIMEOUT_S)
        status["steps"][name] = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000)}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a cold cache is not an error; the page fills it on first use
        msg = " ".join(str(exc).split())[:160] or type(exc).__name__
        status["steps"][name] = {"ok": False, "ms": round((time.perf_counter() - t0) * 1000), "error": msg}
        log.info("warmup: %s skipped: %s", name, msg)


async def _outcome() -> None:
    from app.sim import outcome

    async with tiger.pooled() as conn:
        res = await outcome.outcome(conn, facility_tz())
    if not res.get("prepared"):
        raise RuntimeError(res.get("message") or "replay not prepared")


async def _inventory() -> None:
    from app.api import stats

    data = await stats.snowflake_inventory(force=True)
    if data.get("errors"):
        raise RuntimeError(data["errors"][0])


async def _handoff() -> None:
    from app.deep import handoff
    from app.sim.replay import load_night

    async with tiger.pooled() as conn:
        night = await load_night(conn)
    if night is None:
        raise RuntimeError("no recorded night")
    if await handoff.cached("night", date.fromisoformat(night.date)) is None:
        raise RuntimeError(f"no stored handoff for the night of {night.date} yet")


async def _semantic_model() -> None:
    from app.deep import analyst

    await analyst.ensure_semantic_model()


async def run() -> None:
    settings = get_settings()
    await asyncio.sleep(START_DELAY_S)
    status["phase"] = "running"
    t0 = time.perf_counter()
    jobs = []
    if settings.tiger_configured:
        jobs.append(_step("outcome", _outcome()))
    if settings.snowflake_configured:
        jobs.append(_step("snowflake_inventory", _inventory()))
        jobs.append(_step("analyst_semantic_model", _semantic_model()))
        if settings.tiger_configured:
            jobs.append(_step("handoff_last_night", _handoff()))
    await asyncio.gather(*jobs)
    status.update(phase="done", ms=round((time.perf_counter() - t0) * 1000))
    log.info("warmup: done in %.1f s %s", time.perf_counter() - t0,
             {k: v.get("ms") if v["ok"] else "skipped" for k, v in status["steps"].items()})  # fmt: skip


def start() -> asyncio.Task | None:
    if not get_settings().warmup_enabled:
        return None
    return asyncio.create_task(run(), name="warmup")
