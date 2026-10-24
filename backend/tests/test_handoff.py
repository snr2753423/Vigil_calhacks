"""Shift handoff (SPEC §9 ⑦): shift windows, the deterministic handoff and AI_AGG output validation (no DB)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.deep import handoff
from app.deep.handoff import ResidentShift, ShiftData

TZ = ZoneInfo("America/Los_Angeles")
START, END = handoff.shift_window("night", date(2026, 10, 16), TZ)


def ts(h: int, m: int = 0) -> datetime:
    day = 16 if h >= 12 else 17
    return datetime(2026, 10, day, h, m, tzinfo=TZ).astimezone(UTC)


def resident(rid: int, name: str, **kw) -> ResidentShift:
    base = dict(room=str(100 + rid), unit="East", age=85, sex="F", code_status="FULL", cognition="intact",
                diagnoses=["Hypertension"], start_tier="STABLE", end_tier="STABLE")  # fmt: skip
    return ResidentShift(rid, name, **(base | kw))


def data() -> ShiftData:
    rosa = resident(17, "Rosa Alvarez", end_tier="HIGH", end_score=75,
                    peak={"ts": ts(3, 40), "tier": "CRITICAL", "score": 89, "factors": ["New confusion (note 02:20)"]},
                    alerts=[{"ts": ts(0, 42), "tier": "HIGH", "from_tier": "WATCH", "rule": None, "factors": ["RR 19"]}],
                    notes=[{"ts": ts(2, 20), "role": "CNA", "text": "Seems more confused", "concern": "med",
                            "confusion": "worse"}])  # fmt: skip
    james = resident(110, "James Ortiz", sex="M", end_tier="STABLE", falls=[ts(4, 8)],
                     peak={"ts": ts(4, 8), "tier": "CRITICAL", "score": 100, "factors": ["Suspected fall at 04:06"]},
                     alerts=[{"ts": ts(4, 8), "tier": "CRITICAL", "from_tier": "CRITICAL", "rule": "fall",
                              "factors": ["Suspected fall at 04:06"]}])  # fmt: skip
    doris = resident(33, "Doris Wright", peak={"ts": ts(1, 10), "tier": "STABLE", "score": 30, "factors": []},
                     device={"from": ts(1, 10), "to": ts(4, 58), "text": "Wristband not worn 01:10–04:58 (3 h 48 min)"})  # fmt: skip
    quiet = resident(5, "Quiet Resident", peak={"ts": ts(2), "tier": "STABLE", "score": 10, "factors": []})
    res = {r.resident_id: r for r in (rosa, james, doris, quiet)}
    return ShiftData("night", date(2026, 10, 16), START, END, True, res, 600, "replay_snapshots")


def test_shift_windows_in_facility_time() -> None:
    assert (START.astimezone(TZ).hour, END.astimezone(TZ).hour, END - START) == (23, 7, timedelta(hours=8))
    day = handoff.shift_window("day", date(2026, 10, 16), TZ)
    assert day[0].astimezone(TZ).hour == 7 and day[1] - day[0] == timedelta(hours=8)
    with pytest.raises(ValueError):
        handoff.shift_window("graveyard", date(2026, 10, 16), TZ)


def test_flagged_residents_are_ranked_by_peak() -> None:
    assert [x.name for x in data().flagged] == ["James Ortiz", "Rosa Alvarez", "Doris Wright"]


def test_deterministic_handoff_names_the_events() -> None:
    h = handoff.deterministic(data(), TZ)
    assert h["critical_events"][0]["resident"] == "James Ortiz (210)" and "fall" in h["critical_events"][0]["text"]
    assert h["device_issues"] == [
        {"resident": "Doris Wright (133)", "text": "Wristband not worn 01:10–04:58 (3 h 48 min)"}
    ]
    assert [w["resident"] for w in h["watch_next_shift"]] == ["Rosa Alvarez (117)"]
    assert {r["name"] for r in h["residents"]} == {"James Ortiz", "Rosa Alvarez", "Doris Wright"}


def test_rows_hold_everything_about_a_resident() -> None:
    rows = handoff.shift_rows(data(), TZ)
    assert rows[0].startswith("SHIFT: night shift 23:00–07:00") and "Residents needing attention: 3" in rows[0]
    rosa = next(r for r in rows if "Rosa Alvarez" in r)
    assert "00:42 WATCH→HIGH" in rosa and 'Note 02:20 CNA: "Seems more confused"' in rosa
    assert "AI_EXTRACT: concern med, confusion worse" in rosa


def test_validate_fills_missing_sbar_and_softens() -> None:
    sections = {"critical_events": [{"time": "04:06", "resident": "James Ortiz (210)", "text": "Suspected fall"}],
                "watch_next_shift": [{"resident": "Rosa Alvarez (117)", "reason": "Infection likely", "action": "Check"}],
                "resolved": [], "device_issues": [], "follow_ups": ["Urinalysis for Rosa"]}  # fmt: skip
    sbar = {17: {"situation": "HIGH 75 at end", "background": "87F, heart failure", "assessment": "New confusion",
                 "recommendation": "Check for UTI signs"}, 110: None}  # fmt: skip
    out, fixes = handoff.validate(sections, sbar, data(), TZ)
    assert out["watch_next_shift"][0]["reason"] == "Infection possible"
    assert out["residents"][1]["background"] == "87F, heart failure"  # background is never hedged
    assert any("R-110" in f for f in fixes) and out["residents"][0]["source"] == "template"


def test_validate_rejects_firm_diagnosis() -> None:
    bad = {"critical_events": [], "watch_next_shift": [{"resident": "x", "reason": "Diagnosis: sepsis", "action": ""}],
           "resolved": [], "device_issues": [], "follow_ups": []}  # fmt: skip
    with pytest.raises(ValueError, match="diagnosis"):
        handoff.validate(bad, {}, data(), TZ)
