"""POST /api/checks — a staff member logged a bedside check (SPEC §7d suppression, §11.1 "Mark checked")."""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import TigerConn, facility_tz
from app.db import tiger
from app.scoring import evaluator as ev
from app.sim import facility as fac

router = APIRouter(prefix="/api", tags=["checks"])


class CheckIn(BaseModel):
    resident_id: int
    staff_id: int | None = None  # default: the CNA on duty for the resident's unit
    kind: str = Field("bedside", max_length=40)
    note: str | None = Field(None, max_length=2000)


@router.post("/checks", status_code=201)
async def log_check(body: CheckIn, conn: TigerConn) -> dict:
    row = await tiger.fetchone(conn, "SELECT unit FROM residents WHERE resident_id = %s", (body.resident_id,))
    if row is None:
        raise HTTPException(404, f"no resident {body.resident_id}")
    now = time.time()
    local = datetime.fromtimestamp(now, facility_tz())
    staff_id = body.staff_id or fac.staff_on_duty("CNA", local.hour + local.minute / 60, row[0])
    payload = {"kind": body.kind, "source": "ui"}
    if body.note:
        payload["note"] = body.note
    await tiger.write_events(conn, [(now, body.resident_id, "check_logged", staff_id, payload)])
    if ev.evaluator is not None:
        ev.evaluator.poke()
    return {
        "ts": datetime.fromtimestamp(now, UTC).isoformat(),
        "resident_id": body.resident_id,
        "type": "check_logged",
        "staff_id": staff_id,
        "payload": payload,
    }
