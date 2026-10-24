"""Simulator controls (SPEC §4, §8, §12): pause/start, force night, scenario injectors, simulated band."""

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api import devices
from app.devices.ingest import LIVE_RESIDENTS
from app.scoring import evaluator as ev
from app.sim import injectors, live_loop
from app.state import sim_state
from app.ws.hub import hub, utc_now_iso

router = APIRouter(prefix="/api/sim", tags=["sim"])


async def _changed() -> dict:
    state = sim_state.to_dict()
    await hub.broadcast({"type": "sim", "ts": utc_now_iso(), "sim": state})
    if ev.evaluator is not None:
        ev.evaluator.poke()
    return state


@router.get("")
async def get_sim() -> dict:
    live = live_loop.live
    return {
        "sim": sim_state.to_dict(),
        "live_sim": live.stats.to_dict() if live else None,
        "injections": live.injections() if live else [],
        "evaluator": ev.evaluator.stats.to_dict() if ev.evaluator else None,
    }


@router.post("/pause")
async def pause() -> dict:
    """Freeze the simulator (and with it the queue)."""
    sim_state.paused = True
    return await _changed()


@router.post("/start")
async def start() -> dict:
    sim_state.paused = False
    return await _changed()


class ForceNight(BaseModel):
    enabled: bool = True
    display_clock_offset_s: int | None = Field(None, ge=-86400, le=86400)


@router.post("/force_night")
async def force_night(body: ForceNight | None = None) -> dict:
    """Treat now as night (22:00–06:00 rules) during a daytime demo; optionally shift the displayed clock.
    Stored data stays real UTC."""
    body = body or ForceNight()
    if body.enabled and not sim_state.force_night:
        sim_state.force_night_since = time.time()
    if not body.enabled:
        sim_state.force_night_since = None
    sim_state.force_night = body.enabled
    if body.display_clock_offset_s is not None:
        sim_state.display_clock_offset_s = body.display_clock_offset_s
    return await _changed()


class Inject(BaseModel):
    kind: str = Field(..., description=" | ".join(injectors.KINDS))
    resident_id: int = Field(..., ge=1, le=120)
    minutes: float | None = Field(None, gt=0, le=24 * 60)


@router.post("/inject", status_code=201)
async def inject(body: Inject) -> dict:
    """Start a scenario for one resident now: fall · sepsis_drift · remove_band · bed_exit. A bed exit for R-120
    while his real devices are in use (simulated band off) is recorded through the bed-sensor path instead."""
    if body.kind == "bed_exit" and body.resident_id in LIVE_RESIDENTS and not sim_state.simulated_band:
        # R-120 streams from the real devices: record the exit through the bed-sensor path (hardware fallback)
        return {"kind": "bed_exit", "via": "bed_sensor", **await devices.record_bed_change(body.resident_id, False)}
    live = live_loop.live
    if live is None or live.stats.phase in ("disabled", "standby"):
        raise HTTPException(409, "the live simulator is not running in this backend")
    try:
        scenario = injectors.make(body.kind, body.resident_id, body.minutes)
        live.inject(scenario)
    except injectors.InjectError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    await hub.broadcast({"type": "injection", "ts": utc_now_iso(), **scenario.describe()})
    return scenario.describe()


class Toggle(BaseModel):
    enabled: bool = True


@router.post("/simulated_band")
async def simulated_band(body: Toggle | None = None) -> dict:
    """Hardware fallback: simulate R-120's band and bed sensor in the live loop."""
    sim_state.simulated_band = (body or Toggle()).enabled
    return await _changed()


@router.post("/reset")
async def reset() -> dict:
    """Back to the plain live demo: end all injected scenarios (R-120 back in bed), unpause, night mode and
    display offset off, simulated band off. Stored history is untouched (`make seed` regenerates it)."""
    live = live_loop.live
    cleared = live.clear_injections() if live else 0
    if not sim_state.simulated_band:  # an admin bed exit for R-120 ends too (the real bed sensor would see a return)
        for rid in LIVE_RESIDENTS:
            await devices.record_bed_change(rid, True)
    sim_state.paused = False
    sim_state.force_night = False
    sim_state.force_night_since = None
    sim_state.display_clock_offset_s = 0
    sim_state.simulated_band = False
    return {"sim": await _changed(), "injections_cleared": cleared}
