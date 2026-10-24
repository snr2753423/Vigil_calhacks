"""POST /api/notes: a nurse note, typed or spoken (SPEC §9 ⑥, §11.2 "Add note").

* JSON `{resident_id, text, author_role}` or multipart with `text`: a typed note.
* multipart with an `audio` file (webm from Chrome, mp4/m4a from iPhone Safari): `PUT` to @CORE.NOTE_AUDIO →
  `AI_TRANSCRIBE` → the transcript becomes the note's text. If transcription fails nothing is stored (422/502 with a
  message asking to type the note instead).

The note is stored as a `note_added` event at once (the keyword fallback scores it on the next evaluation), then
`AI_EXTRACT` runs (waited for up to EXTRACT_WAIT_S): its fields replace the fallback for P_concern and NEWS2
consciousness. The response carries the transcript and the extracted fields for the UI's chips; an extraction that
takes longer lands later as an `insight` message on /ws/live.
"""

import asyncio
import logging
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import TigerConn, facility_tz, refuse_in_replay
from app.db import tiger
from app.deep import notes as deep_notes
from app.scoring import evaluator as ev
from app.sim import facility as fac
from app.ws.hub import hub, utc_now_iso

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["notes"])

EXTRACT_WAIT_S = 8.0
_pending: set[asyncio.Task] = set()


class NoteIn(BaseModel):
    resident_id: int
    text: str = Field(..., min_length=1, max_length=4000)
    author_role: str = Field("CNA", pattern="^(CNA|LPN|RN)$")
    staff_id: int | None = None  # default: the staff member of that role on duty for the resident's unit


def _err(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:160] or type(exc).__name__


async def _extract_later(rid: int, note_ts: datetime, text: str, source: str) -> dict:
    """AI_EXTRACT on its own pooled connection (the request's may be gone), then rescore and tell the UI."""
    async with tiger.pooled() as conn:
        payload = await deep_notes.extract_and_store(conn, rid, note_ts, text, source)
    if ev.evaluator is not None:
        ev.evaluator.poke()
    await hub.broadcast({"type": "insight", "kind": "note_extract", "resident_id": rid, "as_of": payload["as_of"],
                         "live": True, "note_ts": note_ts.isoformat()})  # fmt: skip
    return payload


@router.post("/notes", status_code=201)
async def add_note(request: Request, conn: TigerConn) -> dict:
    refuse_in_replay("add a note")
    transcript = None
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        audio = form.get("audio")
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        if audio is not None and not isinstance(audio, str):
            data = await audio.read()
            if not data:
                raise HTTPException(422, "The recording is empty: record again or type the note.")
            if len(data) > deep_notes.MAX_AUDIO_BYTES:
                raise HTTPException(413, "The recording is too long (10 MB max).")
            try:
                ext = deep_notes.audio_extension(audio.content_type, audio.filename)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            # Validate everything but the text (and that the resident exists) before paying for an upload + transcript.
            try:
                rid = NoteIn(**(fields | {"text": "voice note"})).resident_id
            except ValidationError as exc:
                raise HTTPException(422, "Please check the note and try again.") from exc
            if await tiger.fetchone(conn, "SELECT 1 FROM residents WHERE resident_id = %s", (rid,)) is None:
                raise HTTPException(404, f"no resident {rid}")
            try:
                with tempfile.TemporaryDirectory(prefix="vigil-note-") as tmp:
                    transcript = await deep_notes.transcribe(data, ext, rid, Path(tmp))
            except Exception as exc:
                log.warning("notes: transcription failed for R-%s: %s", rid, _err(exc))
                raise HTTPException(
                    502, f"Snowflake AI_TRANSCRIBE failed ({_err(exc)}). Type the note instead."
                ) from exc
            if not transcript["text"]:
                raise HTTPException(422, "No speech was recognised in the recording. Try again or type the note.")
            fields["text"] = transcript["text"]
        try:
            body = NoteIn(**fields)
        except ValidationError as exc:
            raise HTTPException(422, "Please check the note and try again.") from exc
    else:
        try:
            body = NoteIn(**await request.json())
        except (ValidationError, ValueError, TypeError) as exc:  # TypeError: a JSON array or scalar body
            raise HTTPException(422, "Please check the note and try again.") from exc

    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(422, "empty note")
    row = await tiger.fetchone(conn, "SELECT unit FROM residents WHERE resident_id = %s", (body.resident_id,))
    if row is None:
        raise HTTPException(404, f"no resident {body.resident_id}")
    now = time.time()
    local = datetime.fromtimestamp(now, facility_tz())
    staff_id = body.staff_id or fac.staff_on_duty(body.author_role, local.hour + local.minute / 60, row[0])
    source = "voice" if transcript else "text"
    payload = {"text": text, "author_role": body.author_role, "source": source}
    if transcript:
        payload |= {"audio": transcript["stage_path"], "audio_duration": transcript["audio_duration"]}
    note_ts = datetime.fromtimestamp(now, UTC)
    await tiger.write_events(conn, [(now, body.resident_id, "note_added", staff_id, payload)])
    if ev.evaluator is not None:
        ev.evaluator.poke()  # the keyword fallback scores it right away
    event = {"ts": note_ts.isoformat(), "resident_id": body.resident_id, "type": "note_added", "staff_id": staff_id,
             "payload": payload}  # fmt: skip
    await hub.broadcast({"type": "event", "ts": utc_now_iso(), "event": event})

    task = asyncio.create_task(_extract_later(body.resident_id, note_ts, text, source), name="note-extract")
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    task.add_done_callback(
        lambda t: t.cancelled() or t.exception()
    )  # logged below when awaited; never "never retrieved"
    extract, status = None, "ok"
    try:
        extract = await asyncio.wait_for(asyncio.shield(task), EXTRACT_WAIT_S)
    except TimeoutError:
        status = "pending"
    except Exception as exc:
        status = f"Snowflake AI_EXTRACT unavailable ({_err(exc)}); scored with the keyword fallback"
        log.warning("notes: %s", status)
    extract = deep_notes.keyword_payload(text) if extract is None else {k: v for k, v in extract.items() if k != "raw"}
    return event | {"transcript": transcript, "extract": extract, "extract_status": status}
