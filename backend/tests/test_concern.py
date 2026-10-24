"""Note concern (SPEC §7d P_concern, §9⑥): Snowflake extraction first, keyword fallback otherwise."""

from datetime import UTC, datetime

import pytest

from app.scoring.concern import keyword_extract, note_concern
from app.scoring.features import Note

TS = datetime(2026, 10, 16, 4, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "level", "confusion"),
    [
        ("Rosa is more confused than usual, only drank half her fluids.", "med", "worse"),
        ("New confusion this evening, pulling at lines.", "med", "worse"),
        ("Breathing faster and more labored, crackles at both bases.", "high", "unknown"),
        ("Found on the floor beside his bed at 04:11.", "high", "unknown"),
        ("Resting quietly, no concerns noted.", "none", "unknown"),
        ("Baseline confusion, pleasant and redirectable.", "med", "unknown"),
    ],
)
def test_keyword_fallback(text, level, confusion) -> None:
    c = keyword_extract(text)
    assert (c.concern_level, c.confusion_change, c.source) == (level, confusion, "keyword")


def test_p_concern_mapping() -> None:
    assert keyword_extract("more confused than usual").p_concern == 1.0
    assert keyword_extract("refused dinner").p_concern == pytest.approx(0.6)
    assert keyword_extract("slept well").p_concern == 0


def test_snowflake_extraction_wins_over_keywords() -> None:
    note = Note(TS, "more confused than usual")
    c = note_concern(note, {"concern_level": "low", "confusion_change": "same", "one_line_summary": "x"})
    assert c.source == "snowflake" and c.p_concern == pytest.approx(0.3) and not c.new_or_worse_confusion


def test_routine_notes_and_no_note() -> None:
    assert note_concern(None, None) is None
    assert note_concern(Note(TS, "Mild sundowning after dinner", routine=True), None).p_concern == 0
