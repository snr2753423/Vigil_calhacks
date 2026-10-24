"""Replay controls (SPEC §4, §12): prepare, start/resume, pause, seek, speed, stop."""

import contextlib
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import facility_tz, parse_ts
from app.sim import prepare
from app.sim.replay import ReplayError, get_engine

router = APIRouter(prefix="/api/replay", tags=["replay"])


def _fail(exc: ReplayError | prepare.PrepareError) -> HTTPException:
    return HTTPException(getattr(exc, "status", 409), str(exc))


@router.get("")
async def status() -> dict:
    """Replay state (cursor, speed, bookmarks), whether last night is prepared, and the prepare job's progress."""
    eng = get_engine()
    if not eng.active:
        with contextlib.suppress(Exception):  # Tiger down: report what we have
            await eng.refresh_meta()  # cheap: two sim_meta reads; shows a just-finished prepare or a re-seed
    return eng.status() | {"prepare": prepare.progress.to_dict() | {"running": prepare.running()}}


class PrepareBody(BaseModel):
    force_detect: bool = False  # re-run ANOMALY_DETECTION even if the night already has results
    force_cards: bool = False  # regenerate cached Cortex cards
    force_deep: bool = False  # redo cached note extractions and forecasts


@router.post("/prepare", status_code=202)
async def start_prepare(body: PrepareBody | None = None) -> dict:
    """Start precomputing last night's deep-tier results in the background (several minutes); poll GET /api/replay."""
    body = body or PrepareBody()
    try:
        prepare.launch(force_detect=body.force_detect, force_assess=body.force_cards, force_deep=body.force_deep)
    except prepare.PrepareError as exc:
        raise _fail(exc) from exc
    return {"prepare": prepare.progress.to_dict() | {"running": True}}


async def _when(at: str | None, local: str | None) -> datetime | None:
    """An ISO 8601 instant, or "HH:MM" facility time on the replay night."""
    if local:
        eng = get_engine()
        night = eng.night or (await eng.refresh_meta())[0]
        if night is None:
            raise ReplayError("No recorded night to replay: run `make seed` first.")
        return night.at_local(local, facility_tz())
    return parse_ts(at, "at")


class StartBody(BaseModel):
    at: str | None = Field(None, description="ISO 8601 replay time to start from (default: resume, or 22:00)")
    time: str | None = Field(None, description='or "HH:MM" facility time on the replay night, e.g. "01:45"')
    speed: int | None = None
    play: bool = True


@router.post("/start")
async def start(body: StartBody | None = None) -> dict:
    body = body or StartBody()
    try:
        return await get_engine().start(at=await _when(body.at, body.time), speed=body.speed, play=body.play)
    except ReplayError as exc:
        raise _fail(exc) from exc


@router.post("/pause")
async def pause() -> dict:
    return await get_engine().pause()


class SeekBody(BaseModel):
    at: str | None = None
    time: str | None = Field(None, description='"HH:MM" facility time on the replay night')
    bookmark: int | None = Field(None, ge=0, description="index into the bookmarks list")


@router.post("/seek")
async def seek(body: SeekBody) -> dict:
    eng = get_engine()
    try:
        if body.bookmark is not None:
            night, _ = await eng.refresh_meta() if eng.night is None else (eng.night, None)
            if night is None or body.bookmark >= len(night.bookmarks):
                raise HTTPException(404, "no such bookmark")
            at = parse_ts(night.bookmarks[body.bookmark]["ts"])
        else:
            at = await _when(body.at, body.time)
        if at is None:
            raise HTTPException(400, "give `at`, `time` or `bookmark`")
        return await eng.seek(at)
    except ReplayError as exc:
        raise _fail(exc) from exc


class SpeedBody(BaseModel):
    speed: int


@router.post("/speed")
async def speed(body: SpeedBody) -> dict:
    try:
        return await get_engine().set_speed(body.speed)
    except ReplayError as exc:
        raise _fail(exc) from exc


@router.post("/stop")
async def stop() -> dict:
    """Back to live."""
    return await get_engine().stop()
