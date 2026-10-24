"""POST /api/notes: typed (JSON) and spoken (multipart audio → AI_TRANSCRIBE) notes, with AI_EXTRACT and its
keyword fallback. Tiger and Snowflake are faked."""

import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api import notes as notes_api
from app.api.deps import tiger_conn
from app.db import tiger
from app.deep import notes as deep_notes
from app.main import app


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    written: list[tuple] = []
    calls: dict[str, list] = {"extract": [], "transcribe": []}

    async def fetchone(conn, sql, params=None):
        return ("Memory Care",)

    async def write_events(conn, events):
        written.extend(events)
        return len(events)

    async def extract_and_store(conn, rid, note_ts: datetime, text, source, *, known_at=None):
        calls["extract"].append((rid, text, source))
        fields = deep_notes.normalize({"confusion_change": "worse", "oral_intake": "reduced"}, text)
        return fields | {"source": "snowflake", "as_of": note_ts.isoformat(), "raw": {}, "ms": 1400}

    async def transcribe(audio: bytes, ext: str, rid: int, tmp_dir):
        calls["transcribe"].append((len(audio), ext, rid))
        return {"text": "she seems more confused tonight and barely drank", "audio_duration": 2.8,
                "stage_path": f"@CORE.NOTE_AUDIO/r{rid:03d}/x.{ext}", "ms": 1500}  # fmt: skip

    class Pool:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(tiger, "fetchone", fetchone)
    monkeypatch.setattr(tiger, "write_events", write_events)
    monkeypatch.setattr(tiger, "pooled", lambda: Pool())
    monkeypatch.setattr(deep_notes, "extract_and_store", extract_and_store)
    monkeypatch.setattr(deep_notes, "transcribe", transcribe)
    app.dependency_overrides[tiger_conn] = lambda: None
    yield written, calls
    app.dependency_overrides.pop(tiger_conn, None)


def test_typed_note_is_stored_then_extracted(client: TestClient, fake) -> None:
    written, calls = fake
    r = client.post("/api/notes", json={"resident_id": 17, "text": "more  confused tonight", "author_role": "CNA"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert written[0][1:3] == (17, "note_added") and written[0][4]["text"] == "more confused tonight"
    assert written[0][4]["source"] == "text" and body["transcript"] is None
    assert body["extract"]["confusion_change"] == "worse" and body["extract"]["source"] == "snowflake"
    assert "raw" not in body["extract"] and body["extract_status"] == "ok"
    assert calls["extract"] == [(17, "more confused tonight", "text")]


def test_voice_note_is_transcribed_then_extracted(client: TestClient, fake) -> None:
    written, calls = fake
    r = client.post(
        "/api/notes",
        data={"resident_id": "17", "author_role": "CNA"},
        files={"audio": ("note.m4a", b"\x00" * 2048, "audio/mp4")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert calls["transcribe"] == [(2048, "m4a", 17)]
    assert body["transcript"]["text"] == "she seems more confused tonight and barely drank"
    assert written[0][4]["source"] == "voice" and written[0][4]["audio"].startswith("@CORE.NOTE_AUDIO/r017/")
    assert body["extract"]["confusion_change"] == "worse" and body["extract"]["oral_intake"] == "reduced"


def test_transcription_failure_stores_nothing(client: TestClient, fake, monkeypatch: pytest.MonkeyPatch) -> None:
    written, _ = fake

    async def broken(*a, **kw):
        raise RuntimeError("warehouse suspended")

    monkeypatch.setattr(deep_notes, "transcribe", broken)
    r = client.post("/api/notes", data={"resident_id": "17"}, files={"audio": ("n.webm", b"\x01" * 10, "audio/webm")})
    assert r.status_code == 502 and "Type the note instead" in r.json()["detail"] and written == []


def test_extraction_failure_falls_back_to_keywords(client: TestClient, fake, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*a, **kw):
        raise RuntimeError("Snowflake down")

    monkeypatch.setattr(deep_notes, "extract_and_store", broken)
    r = client.post("/api/notes", json={"resident_id": 17, "text": "Rosa seems more confused than usual"})
    body = r.json()
    assert r.status_code == 201 and body["extract"]["source"] == "keyword"
    assert body["extract"]["confusion_change"] == "worse" and "keyword fallback" in body["extract_status"]


def test_slow_extraction_answers_pending(client: TestClient, fake, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*a, **kw):
        await asyncio.sleep(0.5)
        return {}

    monkeypatch.setattr(deep_notes, "extract_and_store", slow)
    monkeypatch.setattr(notes_api, "EXTRACT_WAIT_S", 0.05)
    r = client.post("/api/notes", json={"resident_id": 17, "text": "ate well"})
    assert r.json()["extract_status"] == "pending" and r.json()["extract"]["source"] == "keyword"


def test_unsupported_or_empty_audio(client: TestClient, fake) -> None:
    assert client.post("/api/notes", data={"resident_id": "17"},
                       files={"audio": ("n.mov", b"\x01", "video/quicktime")}).status_code == 422  # fmt: skip
    assert client.post("/api/notes", data={"resident_id": "17"},
                       files={"audio": ("n.webm", b"", "audio/webm")}).status_code == 422  # fmt: skip


def test_voice_note_is_validated_before_transcription(
    client: TestClient, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, calls = fake
    audio = {"audio": ("n.webm", b"\x01" * 10, "audio/webm")}
    assert client.post("/api/notes", data={"resident_id": "17", "author_role": "MD"}, files=audio).status_code == 422
    assert client.post("/api/notes", data={}, files=audio).status_code == 422  # no resident

    async def no_resident(conn, sql, params=None):
        return None

    monkeypatch.setattr(tiger, "fetchone", no_resident)
    assert client.post("/api/notes", data={"resident_id": "999"}, files=audio).status_code == 404
    assert calls["transcribe"] == []  # nothing uploaded or transcribed


def test_non_object_json_is_a_422(client: TestClient, fake) -> None:
    assert client.post("/api/notes", json=[]).status_code == 422
    assert client.post("/api/notes", json="more confused").status_code == 422
