"""Nurse notes: AI_TRANSCRIBE + AI_EXTRACT (SPEC §9 ⑥).

* Text note → `AI_EXTRACT` (SQL REST API) with the fields below.
* Voice note (webm from Chrome, mp4/m4a from iPhone Safari) → `PUT` to `@CORE.NOTE_AUDIO` (Python connector; the SQL
  API has no PUT) → `AI_TRANSCRIBE` (SQL REST API) → the text path.

The extraction is stored in Tiger `ai_insights` (kind = note_extract, as_of = when it was known, never before the
note) and in Snowflake `CORE.NOTE_EXTRACTS`. The fast tier reads it for the latest note within 12 h
(`scoring/concern.py`): new or worse confusion → NEWS2 consciousness 3 and P_concern 1; else concern_level → P_concern.
Until an extraction exists, or when Snowflake fails, the keyword fallback in `scoring/concern.py` scores the text.

`concern_level` is derived deterministically from the extracted findings (see `normalize`).

AI_EXTRACT's JSON schema takes string fields only (an enum, integer or boolean is refused: "Incorrect 2nd-level
type"), so each question names its allowed answers and `normalize` maps the answer onto them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from app.db import snowflake_sql as sf
from app.db import tiger
from app.db.snowflake_rest import client as rest_client
from app.deep.sync import naive_utc
from app.scoring.concern import keyword_extract

log = logging.getLogger(__name__)

AUDIO_STAGE = "@CORE.NOTE_AUDIO"
EXTRACT_TIMEOUT_S = 20.0
TRANSCRIBE_TIMEOUT_S = 60.0
# The replay night's notes were written before Vigil existed: their extractions are stamped as known this long after
# the note (a live AI_EXTRACT call takes ~1.5 s, plus the round trips).
PREPARED_DELAY = timedelta(seconds=30)
AUDIO_TYPES = {  # browser MIME type → file extension AI_TRANSCRIBE accepts
    "audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "video/mp4": "m4a",
    "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/aac": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/wave": "wav", "audio/flac": "flac",
}  # fmt: skip
MAX_AUDIO_BYTES = 10 * 1024 * 1024

FIELDS: dict[str, str] = {
    "confusion_change": (
        "Compared with the resident's usual state, is their confusion new, worse, same, better, or unknown? "
        "Answer one word. 'more confused than usual' = worse. Not mentioned = unknown."
    ),
    "oral_intake": (
        "Eating and drinking: normal, reduced, refused, or unknown? Answer one word. 'only drank half', "
        "'barely drank', 'ate little' = reduced. Not mentioned = unknown."
    ),
    "pain_0_10": "Pain score from 0 to 10 if the note states or clearly implies one, else unknown.",
    "urinary_symptoms": "Urinary symptoms described (e.g. burning, frequency, dark or smelly urine, incontinence), or none.",
    "fall_or_near_fall": "Does the note describe a fall or a near-fall? Answer yes or no.",
    "breathing_concern": "Does the note describe a breathing problem (fast, labored, shallow, short of breath)? yes or no.",
    "mood_behavior": "Mood or behavior changes described (e.g. restless, agitated, withdrawn), in a few words, or none.",
    "fever_or_warm": "Does the note describe a fever or the resident feeling warm or hot to touch? Answer yes or no.",
    "concern_level": (
        "Overall concern for the nurse reading this note. Answer exactly one of: none, low, med, high. "
        "none = routine or nothing about the resident's condition (slept well, ate well, a wristband or other "
        "equipment issue, a comfort request). low = a minor change to watch (a bit restless, drank or ate somewhat less). "
        "med = a new change to check soon (new or worse confusion, warm to touch or fever, refusing fluids, new pain). "
        "high = urgent (a fall, breathing difficulty, not rousing, chest pain)."
    ),
    "one_line_summary": "Restate the note in one short plain sentence for a handoff.",
}
EXTRACT_FORMAT = {"schema": {"type": "object", "properties": {k: {"type": "string", "description": v}
                                                               for k, v in FIELDS.items()}}}  # fmt: skip

_NONE = {"", "none", "null", "n/a", "na", "unknown", "not mentioned", "not stated", "no"}


def _s(v) -> str:
    return " ".join(str(v if v is not None else "").split()).strip(" .\"'")


def _enum(v, mapping: list[tuple[str, str]], default: str) -> str:
    t = _s(v).lower()
    if t in _NONE:
        return default
    for pattern, value in mapping:
        if re.search(pattern, t):
            return value
    return default


_NO_PREFIXES = ("no", "false", "not ", "none", "unknown", "n/a", "denies")


def _yes(v) -> bool:
    """A yes/no question's answer. AI_EXTRACT often answers with the phrase it found ("found on the floor beside
    his bed", "breathing faster and more labored") instead of "yes": any answer that isn't a no counts as yes."""
    t = _s(v).lower()
    return bool(t) and t not in _NONE and not t.startswith(_NO_PREFIXES)


def normalize(raw: dict | None, text: str) -> dict:
    """AI_EXTRACT's answers → Vigil's enums (SPEC §9 ⑥). Missing or unclear answers become unknown/none."""
    r = raw or {}
    confusion = _enum(r.get("confusion_change"), [(r"\bnew", "new"), (r"wors|more|increas", "worse"),
                      (r"better|improv|less", "better"), (r"same|usual|baseline|no change|unchanged", "same")],
                      "unknown")  # fmt: skip
    intake = _enum(r.get("oral_intake"), [(r"refus", "refused"), (r"reduc|half|less|poor|barely|little|low", "reduced"),
                   (r"normal|good|well|all", "normal")], "unknown")  # fmt: skip
    pain_m = re.search(r"\b(10|[0-9])\b", _s(r.get("pain_0_10")))
    pain = int(pain_m.group(1)) if pain_m else None
    urinary = _s(r.get("urinary_symptoms"))
    mood = _s(r.get("mood_behavior"))
    fall, breathing, warm = (_yes(r.get(k)) for k in ("fall_or_near_fall", "breathing_concern", "fever_or_warm"))
    ai_level = _enum(r.get("concern_level"), [(r"high|urgent|severe", "high"), (r"med|moderate", "med"),
                     (r"low|minor|mild", "low"), (r"none|routine", "none")], "unknown")  # fmt: skip
    # concern_level is Vigil's deterministic reading of the extracted findings (measured: AI_EXTRACT's own overall
    # rating called a wristband note "med" and "slept well" "low"). Its rating can only raise a finding-backed
    # note to high.
    if fall or breathing:
        level = "high"
    elif (
        confusion in ("new", "worse") or intake == "refused" or warm or (pain or 0) >= 5 or urinary.lower() not in _NONE
    ):
        level = "med"
    elif intake == "reduced" or mood.lower() not in _NONE or (pain or 0) > 0:
        level = "low"
    else:
        level = "none"
    if ai_level == "high" and level in ("low", "med"):
        level = "high"
    summary = _s(r.get("one_line_summary"))
    if summary.lower() in _NONE:
        summary = text[:160]
    return {
        "confusion_change": confusion,
        "oral_intake": intake,
        "pain_0_10": pain,
        "urinary_symptoms": None if urinary.lower() in _NONE else urinary,
        "fall_or_near_fall": fall,
        "breathing_concern": breathing,
        "mood_behavior": None if mood.lower() in _NONE else mood,
        "fever_or_warm": warm,
        "concern_level": level,
        "ai_concern_level": ai_level,
        "one_line_summary": summary,
    }


def keyword_payload(text: str) -> dict:
    """The keyword fallback in the same shape (shown when Snowflake is down; scoring uses it automatically)."""
    k = keyword_extract(text)
    return {
        "confusion_change": k.confusion_change, "oral_intake": "unknown", "pain_0_10": None,
        "urinary_symptoms": None, "fall_or_near_fall": False, "breathing_concern": False, "mood_behavior": None,
        "fever_or_warm": False, "ai_concern_level": "unknown",
        "concern_level": k.concern_level, "one_line_summary": text[:160], "source": "keyword",
    }  # fmt: skip


# --------------------------------------------------------------------------- Snowflake


async def ai_extract(text: str, *, timeout: float = EXTRACT_TIMEOUT_S) -> tuple[dict, dict]:
    """→ (normalized fields, raw AI_EXTRACT response). Raises on any Snowflake failure."""
    rows = await rest_client().sql(
        "SELECT AI_EXTRACT(text => ?, responseFormat => PARSE_JSON(?))",
        [text, json.dumps(EXTRACT_FORMAT)],
        feature="sql_api.note_extract",
        timeout=timeout,
    )
    body = json.loads(rows[0][0])
    if body.get("error"):
        raise RuntimeError(f"AI_EXTRACT: {body['error']}")
    raw = body.get("response") or {}
    return normalize(raw, text), raw


def audio_extension(content_type: str | None, filename: str | None) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    if base in AUDIO_TYPES:
        return AUDIO_TYPES[base]
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in set(AUDIO_TYPES.values()) | {"mp4"}:
        return "m4a" if ext == "mp4" else ext
    raise ValueError(f"unsupported audio type {content_type or filename!r}; record webm, m4a/mp4, ogg, wav or mp3")


def _put(local: Path, stage_dir: str) -> None:
    def go(conn) -> None:
        sf.query(conn, f"PUT 'file://{local.as_posix()}' {AUDIO_STAGE}/{stage_dir}/ AUTO_COMPRESS = FALSE OVERWRITE = TRUE",
                 feature="notes.put_audio")  # fmt: skip

    sf.shared.run(go)


async def transcribe(audio: bytes, ext: str, rid: int, tmp_dir: Path) -> dict:
    """Upload one recording and transcribe it. → {text, audio_duration, stage_path, ms}."""
    t0 = time.perf_counter()
    name = f"{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}.{ext}"
    stage_dir = f"r{rid:03d}"
    local = tmp_dir / name
    local.write_bytes(audio)
    try:
        await asyncio.to_thread(_put, local, stage_dir)
    finally:
        local.unlink(missing_ok=True)
    rel = f"{stage_dir}/{name}"
    rows = await rest_client().sql(
        f"SELECT AI_TRANSCRIBE(TO_FILE('{AUDIO_STAGE}', ?))", [rel], feature="sql_api.transcribe",
        timeout=TRANSCRIBE_TIMEOUT_S,
    )  # fmt: skip
    body = json.loads(rows[0][0])
    text = " ".join(str(body.get("text") or "").split())
    return {"text": text, "audio_duration": body.get("audio_duration"), "stage_path": f"{AUDIO_STAGE}/{rel}",
            "ms": round((time.perf_counter() - t0) * 1000)}  # fmt: skip


async def store_snowflake(rid: int, note_ts: datetime, source: str, text: str, payload: dict) -> None:
    try:
        await rest_client().sql(
            """INSERT INTO CORE.NOTE_EXTRACTS (RESIDENT_ID, NOTE_TS, SOURCE, TEXT, CONFUSION_CHANGE, ORAL_INTAKE,
                                               CONCERN_LEVEL, PAYLOAD, EXTRACTED_AT)
               SELECT ?, ?::TIMESTAMP_NTZ, ?, ?, ?, ?, ?, PARSE_JSON(?), SYSDATE()""",
            [rid, naive_utc(note_ts).isoformat(sep=" ", timespec="milliseconds"), source, text,
             payload["confusion_change"], payload["oral_intake"], payload["concern_level"], json.dumps(payload)],
            feature="sql_api.store_note_extract",
            timeout=20,
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 — Tiger has it; this copy is for Cortex Analyst / audit
        log.warning("notes: storing R-%d in CORE.NOTE_EXTRACTS failed: %s", rid, " ".join(str(exc).split())[:160])


async def extract_and_store(
    conn: psycopg.AsyncConnection,
    rid: int,
    note_ts: datetime,
    text: str,
    source: str,
    *,
    known_at: datetime | None = None,
) -> dict:
    """AI_EXTRACT one note and store it (Tiger now; Snowflake in the background). → the stored payload."""
    t0 = time.perf_counter()
    fields, raw = await ai_extract(text)
    ms = round((time.perf_counter() - t0) * 1000)
    at = known_at or max(datetime.now(UTC), note_ts)
    payload = fields | {"source": "snowflake", "model": "AI_EXTRACT", "note_ts": note_ts.isoformat(),
                        "note_source": source, "text": text, "raw": raw, "ms": ms}  # fmt: skip
    await tiger.upsert_insights(conn, "note_extract", [(rid, at, payload)])
    _background(store_snowflake(rid, note_ts, source, text, payload))
    return payload | {"as_of": at.isoformat()}


_tasks: set[asyncio.Task] = set()


def _background(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --------------------------------------------------------------------------- replay prepare


async def prepare_night_notes(
    conn: psycopg.AsyncConnection, start: datetime, end: datetime, *, force: bool = False, say=log.info
) -> dict:
    """AI_EXTRACT every non-routine note in [start, end] (the replay night's cast notes), stamped as known
    PREPARED_DELAY after the note, so replay scores them from real extractions. Cached ones are kept unless `force`."""
    notes = await tiger.fetch_dicts(
        conn,
        """SELECT ts, resident_id, payload FROM events
           WHERE type = 'note_added' AND ts >= %s AND ts <= %s AND NOT coalesce((payload->>'routine')::bool, false)
           ORDER BY ts""",
        (start, end),
    )
    have = {
        (r["resident_id"], r["note_ts"])
        for r in await tiger.fetch_dicts(
            conn,
            """SELECT resident_id, payload->>'note_ts' AS note_ts FROM ai_insights
               WHERE kind = 'note_extract' AND as_of >= %s AND as_of <= %s AND payload->>'source' = 'snowflake'""",
            (start, end + PREPARED_DELAY),
        )
    }
    todo = [n for n in notes if force or (n["resident_id"], n["ts"].isoformat()) not in have]
    out: list[dict] = []
    failed = 0
    for n in todo:  # a handful of notes, ~1.5 s each
        text = (n["payload"] or {}).get("text") or ""
        try:
            p = await extract_and_store(conn, n["resident_id"], n["ts"], text, (n["payload"] or {}).get("source") or "text",
                                        known_at=n["ts"] + PREPARED_DELAY)  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            failed += 1
            say(
                f"  note         R-{n['resident_id']} {n['ts']:%H:%M}Z: AI_EXTRACT failed ({' '.join(str(exc).split())[:120]})"
            )
            continue
        out.append({"resident_id": n["resident_id"], "ts": n["ts"].isoformat(), "confusion_change": p["confusion_change"],
                    "concern_level": p["concern_level"], "oral_intake": p["oral_intake"]})  # fmt: skip
        say(f"  note         R-{n['resident_id']} {n['ts']:%H:%M}Z → confusion {p['confusion_change']}, "
            f"concern {p['concern_level']}, intake {p['oral_intake']}: {text[:60]}")  # fmt: skip
    await conn.commit()
    return {"notes": len(notes), "extracted": len(out), "cached": len(notes) - len(todo), "failed": failed,
            "results": out}  # fmt: skip
