"""Live devices (SPEC §10, §11.5): status for the /devices page and the admin bed-state fallback."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import Settings
from app.devices.ingest import LIVE_RESIDENTS, ingest
from app.sim import facility as fac
from app.state import sim_state

router = APIRouter(prefix="/api/devices", tags=["devices"])


def public_url() -> str | None:
    """PUBLIC_URL, re-read from .env on every call so pasting a fresh tunnel URL needs no backend restart."""
    url = (Settings().public_url or "").strip().rstrip("/")
    return url or None


@router.get("")
async def devices() -> dict:
    base = public_url()
    rid = fac.LIVE_RESIDENT_ID
    return {
        "live_resident_id": rid,
        "public_url": base,
        "bed_url": f"{base}/bed/{rid}" if base else None,
        "simulated_band": sim_state.simulated_band,
        **ingest.status(),
    }


class BedState(BaseModel):
    in_bed: bool


async def record_bed_change(resident_id: int, in_bed: bool) -> dict:
    """Record a bed exit / return for the live resident now, as if the bed sensor had seen it (admin fallback;
    also used by the scenario injector and demo reset)."""
    if resident_id not in LIVE_RESIDENTS:
        raise HTTPException(404, f"R-{resident_id} has no live bed sensor")
    if sim_state.simulated_band:
        raise HTTPException(409, "the simulated band is on: use the scenario injector instead")
    await ingest.restore(resident_id)  # after a restart, know whether the last stored event was an exit
    t = ingest.set_bed(resident_id, in_bed)
    return {"resident_id": resident_id, "in_bed": in_bed, "changed": t is not None, "event": t.kind if t else None}


@router.post("/bed/{resident_id}/state")
async def set_bed_state(resident_id: int, body: BedState) -> dict:
    """Admin fallback for the iPhone sensor: record a bed exit / return for the live resident now."""
    return await record_bed_change(resident_id, body.in_bed)
