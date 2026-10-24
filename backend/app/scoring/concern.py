"""Staff concern from the latest nurse note: P_concern and NEWS2 consciousness (SPEC §7d, §9⑥).

The real extraction is Snowflake AI_EXTRACT (Phase 6), cached in Tiger `ai_insights` (kind=note_extract).
Until one exists for a note, or if Snowflake is down, a deterministic keyword fallback reads the note text
so a typed "more confused than usual" still counts (AGENTS.md rule 5: graceful failure).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.scoring.features import Note

CONCERN_P = {"none": 0.0, "low": 0.3, "med": 0.6, "high": 1.0}

_CONFUSION_WORSE = re.compile(
    r"\b(more|newly|new|increasing(ly)?|acute(ly)?|worse|suddenly)\b[^.;]{0,40}\bconfus"
    r"|\bconfus\w*[^.;]{0,40}\b(than usual|worse|new|today|tonight)\b",
    re.IGNORECASE,
)
_HIGH = re.compile(
    r"not rousing|unresponsive|found on the floor|\bfell\b|\bfall\b|chest pain|labou?red|short of breath"
    r"|blue|cyanos|seiz",
    re.IGNORECASE,
)
_MED = re.compile(
    r"confus|drank (only )?half|only drank|refus|warm to touch|fever|restless|swollen|breathing faster"
    r"|crackles|vomit|pain|dizz|agitat|not eating",
    re.IGNORECASE,
)
# A note whose only findings are restlessness or reduced intake, with the restlessness itself hedged ("a bit
# restless at bedtime, only drank half her fluids"), is low concern; unhedged, or hedged elsewhere, stays med.
_HEDGED_RESTLESS = re.compile(r"\b(a bit|a little|slightly|somewhat) restless", re.IGNORECASE)
_MILD = re.compile(r"restless|drank (only )?half|only drank", re.IGNORECASE)


@dataclass(frozen=True)
class NoteConcern:
    concern_level: str  # none | low | med | high
    confusion_change: str  # new | worse | same | better | unknown
    source: str  # snowflake | keyword
    summary: str = ""

    @property
    def new_or_worse_confusion(self) -> bool:
        return self.confusion_change in ("new", "worse")

    @property
    def p_concern(self) -> float:
        """none 0, low .3, med .6, high 1; new/worse confusion → 1 (SPEC §7d)."""
        return 1.0 if self.new_or_worse_confusion else CONCERN_P.get(self.concern_level, 0.0)


def keyword_extract(text: str) -> NoteConcern:
    confusion = "worse" if _CONFUSION_WORSE.search(text) else "unknown"
    if _HIGH.search(text):
        level = "high"
    elif confusion == "worse":
        level = "med"
    elif meds := [m.group(0) for m in _MED.finditer(text)]:
        level = "low" if _HEDGED_RESTLESS.search(text) and all(_MILD.fullmatch(m) for m in meds) else "med"
    else:
        level = "none"
    return NoteConcern(level, confusion, "keyword", text[:120])


def note_concern(note: Note | None, extract: dict | None) -> NoteConcern | None:
    """Concern from the latest note: the Snowflake extraction when present, else the keyword fallback."""
    if note is None:
        return None
    if extract:
        return NoteConcern(
            str(extract.get("concern_level") or "none"),
            str(extract.get("confusion_change") or "unknown"),
            "snowflake",
            str(extract.get("one_line_summary") or ""),
        )
    if note.routine:
        return NoteConcern("none", "unknown", "keyword", note.text[:120])
    return keyword_extract(note.text)
