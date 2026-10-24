"""POST /api/notes — a nurse note typed in the UI (SPEC §9 ⑥, §11.2 "Add note").

Text only for now: stored as a `note_added` event, which the scoring engine reads for P_concern and NEWS2
consciousness (keyword fallback until Snowflake AI_EXTRACT lands in Phase 6, which also adds voice notes).
"""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import TigerConn, facility_tz, refuse_in_replay
from app.db import tiger
from app.scoring import evaluator as ev
from app.sim import facility as fac
from app.ws.hub import hub, utc_now_iso

router = APIRouter(prefix="/api", tags=["notes"])


class NoteIn(BaseModel):
    resident_id: int
    text: str = Field(..., min_length=1, max_length=4000)
    author_role: str = Field("CNA", pattern="^(CNA|LPN|RN)$")
    staff_id: int | None = None  # default: the staff member of that role on duty for the resident's unit


@router.post("/notes", status_code=201)
async def add_note(body: NoteIn, conn: TigerConn) -> dict:
    refuse_in_replay("add a note")
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(422, "empty note")
    row = await tiger.fetchone(conn, "SELECT unit FROM residents WHERE resident_id = %s", (body.resident_id,))
    if row is None:
        raise HTTPException(404, f"no resident {body.resident_id}")
    now = time.time()
    local = datetime.fromtimestamp(now, facility_tz())
    staff_id = body.staff_id or fac.staff_on_duty(body.author_role, local.hour + local.minute / 60, row[0])
    payload = {"text": text, "author_role": body.author_role, "source": "text"}
    await tiger.write_events(conn, [(now, body.resident_id, "note_added", staff_id, payload)])
    if ev.evaluator is not None:
        ev.evaluator.poke()
    event = {
        "ts": datetime.fromtimestamp(now, UTC).isoformat(),
        "resident_id": body.resident_id,
        "type": "note_added",
        "staff_id": staff_id,
        "payload": payload,
    }
    await hub.broadcast({"type": "event", "ts": utc_now_iso(), "event": event})
    return event
