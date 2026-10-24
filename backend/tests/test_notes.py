"""Nurse-note extraction normalising (SPEC §9 ⑥): AI_EXTRACT answers → Vigil's fields and concern level."""

import pytest

from app.deep import notes
from app.scoring.concern import note_concern
from app.scoring.features import Note
from tests.scoring_helpers import NIGHT


def test_confusion_and_intake_map_onto_the_enums() -> None:
    x = notes.normalize({"confusion_change": "worse", "oral_intake": "reduced", "concern_level": "med"},
                        "she seems more confused tonight and barely drank")  # fmt: skip
    assert x["confusion_change"] == "worse" and x["oral_intake"] == "reduced" and x["concern_level"] == "med"
    assert notes.normalize({"confusion_change": "None"}, "t")["confusion_change"] == "unknown"
    assert notes.normalize({"confusion_change": "New onset"}, "t")["confusion_change"] == "new"
    assert notes.normalize({"oral_intake": "only drank half"}, "t")["oral_intake"] == "reduced"


@pytest.mark.parametrize(
    ("raw", "level"),
    [
        ({"mood_behavior": "restless", "oral_intake": "reduced", "concern_level": "low"}, "low"),  # Alvarez 21:30
        ({"confusion_change": "worse", "fever_or_warm": "yes", "concern_level": "med"}, "med"),  # Alvarez 02:20
        ({"breathing_concern": "breathing faster and more labored", "concern_level": "high"}, "high"),  # Lee
        ({"fall_or_near_fall": "James found on the floor beside his bed"}, "high"),  # Ortiz: the phrase, not "yes"
        ({"concern_level": "med"}, "none"),  # a wristband note: no findings, whatever the AI's overall rating
        ({"mood_behavior": "None", "concern_level": "low"}, "none"),  # "slept well"
        ({"mood_behavior": "restless", "concern_level": "high"}, "high"),  # the AI may raise a finding-backed note
    ],
)
def test_concern_level_follows_the_findings(raw: dict, level: str) -> None:
    assert notes.normalize(raw, "note")["concern_level"] == level


def test_yes_no_answers() -> None:
    assert notes._yes("Yes.") and notes._yes("found on the floor")
    assert not any(notes._yes(v) for v in ("No", "none", "no fall mentioned", "Not mentioned", "", "unknown"))


def test_summary_falls_back_to_the_text() -> None:
    assert notes.normalize({"one_line_summary": "None"}, "Ate well.")["one_line_summary"] == "Ate well."


def test_extract_drives_scoring_and_keyword_payload_has_the_same_shape() -> None:
    fields = notes.normalize({"confusion_change": "worse"}, "more confused")
    c = note_concern(Note(NIGHT, "more confused"), fields)
    assert c.source == "snowflake" and c.new_or_worse_confusion and c.p_concern == 1.0
    assert set(notes.keyword_payload("more confused than usual")) >= set(fields)


@pytest.mark.parametrize(
    ("ctype", "name", "ext"),
    [("audio/webm;codecs=opus", "note.webm", "webm"), ("audio/mp4", "note.m4a", "m4a"), ("", "note.mp4", "m4a"),
     ("application/octet-stream", "note.wav", "wav")],
)  # fmt: skip
def test_audio_extension(ctype: str, name: str, ext: str) -> None:
    assert notes.audio_extension(ctype, name) == ext


def test_unsupported_audio_is_refused() -> None:
    with pytest.raises(ValueError):
        notes.audio_extension("video/quicktime", "note.mov")
