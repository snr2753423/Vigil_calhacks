"""Shift handoff via AI_AGG with SBAR sections (SPEC §9 ⑦).

1. `gather` reads the shift from Tiger: each resident's tier changes and peak score (`replay_snapshots` for the
   prepared replay night, `priority_snapshots` otherwise), Vigil alerts (the prepare pass's for the replay night, the
   live `alert_raised` events otherwise), checks, non-routine notes with their AI_EXTRACT fields, falls, bed exits,
   device issues (a device factor on the resident's score) and forecast callouts.
2. `shift_rows` writes one SHIFT row plus one RESIDENT row per flagged resident (HIGH/CRITICAL, an alert, a fall, a
   device issue of ≥ 30 min or a note of med+ concern); `event_rows` splits each flagged resident's shift into
   records (profile, tier changes, alerts, checks, notes, forecasts, device).
3. Two `AI_AGG` calls in parallel through the SQL REST API, on rows sent as a JSON array and FLATTENed in Snowflake:
   the sections (critical events, watch next shift, resolved, device issues, follow-ups) over the shift rows, and one
   SBAR line per resident over the event records `GROUP BY` resident. (Asked for both in one call, AI_AGG left the
   per-resident SBAR out.) The JSON is validated and its wording softened (`wording.py`); a flagged resident without
   a usable SBAR gets the deterministic one.
4. Stored in Snowflake `CORE.HANDOFFS` and cached in this process. `deterministic` builds the same sections from the
   same data when Snowflake is slow or down (AGENTS.md rule 5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.db import tiger
from app.db.snowflake_rest import client as rest_client
from app.db.snowflake_rest import parse_json_text
from app.deep.assess import CODE_STATUS_ESCALATION, TIER_HEADLINE
from app.deep.wording import soften, violations
from app.scoring import trend
from app.scoring.priority import TIER_RANK
from app.sim.replay import load_prepared

log = logging.getLogger(__name__)

SHIFTS = {"day": (7, 15), "evening": (15, 23), "night": (23, 7)}  # local start/end hour (SPEC §2)
AGG_TIMEOUT_S = 90.0
MAX_DETAILED = 14  # residents with a full RESIDENT row; the rest of the WATCH residents share one summary line
MODEL = "SNOWFLAKE.CORTEX.AI_AGG"
DEVICE_MIN = timedelta(minutes=30)  # a shorter wristband gap (charging, a shower) is not a handoff item

INSTRUCTION = """You are writing the end-of-shift handoff for the oncoming nurse at a skilled nursing facility. \
The rows are Vigil's shift records: one SHIFT row, then one RESIDENT row per resident who needed attention. Use ONLY \
facts in the rows; never invent findings, vital signs, orders or times. Decision-support language: "possible", \
"consider", "check"; never "likely" or "probably", never diagnose, never say Vigil prevents harm or replaces staff. \
Respect each resident's code status. Name residents as "First Last (room)".

A fall is a "suspected fall" unless a note says the resident was found on the floor; never write "confirmed".

Return ONLY one JSON object (no prose, no code fences) with these keys:
"critical_events": [{"time": "HH:MM", "resident": "...", "text": "..."}] CRITICAL alerts and falls, in time order.
"watch_next_shift": [{"resident": "...", "reason": "...", "action": "..."}] residents still HIGH or WATCH at the end \
of the shift or with an open concern, most urgent first.
"resolved": [{"resident": "...", "text": "..."}] alerts that resolved during the shift.
"device_issues": [{"resident": "...", "text": "..."}] wristband or sensor problems.
"follow_ups": ["..."] concrete tasks for the next shift that the rows mention (labs pending, provider or family \
calls, neuro checks); empty if none.
Keep every text short (at most 25 words)."""

SBAR_INSTRUCTION = """You are writing one resident's line in the end-of-shift handoff at a skilled nursing facility, \
in SBAR form, from Vigil's records for this resident (profile, tier changes, alerts, checks, notes, forecasts, \
device issues). Use ONLY facts in the rows; never invent findings, vital signs, orders or times. Decision-support \
language: "possible", "consider", "check"; never "likely" or "probably", never diagnose. A fall is a "suspected \
fall" unless a note says the resident was found on the floor. Respect the code status.

Return ONLY one JSON object (no prose, no code fences): {"situation": "...", "background": "...", \
"assessment": "...", "recommendation": "..."}. Situation: what happened this shift and where they are now. \
Background: age, key diagnoses, code status. Assessment: the signals behind it, with numbers from the rows. \
Recommendation: what the next shift should check or do. Each field one sentence, at most 25 words."""


def shift_window(shift: str, day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) in UTC of a shift that starts on local `day` (the night shift ends the next morning)."""
    if shift not in SHIFTS:
        raise ValueError(f"shift must be one of {', '.join(SHIFTS)}")
    h0, h1 = SHIFTS[shift]
    start = datetime(day.year, day.month, day.day, h0, tzinfo=tz)
    end_day = day + timedelta(days=1) if h1 <= h0 else day
    end = datetime(end_day.year, end_day.month, end_day.day, h1, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


# --------------------------------------------------------------------------- gather


@dataclass
class ResidentShift:
    resident_id: int
    name: str
    room: str
    unit: str
    age: int | None
    sex: str
    code_status: str
    cognition: str
    diagnoses: list[str]
    start_tier: str | None = None
    start_score: float | None = None
    end_tier: str | None = None
    end_score: float | None = None
    peak: dict | None = None  # {ts, tier, score, factors}
    changes: list[dict] = field(default_factory=list)  # [{ts, tier, score}]
    alerts: list[dict] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)
    checks: int = 0
    last_check: datetime | None = None
    bed_exits: int = 0
    notes: list[dict] = field(default_factory=list)
    falls: list[datetime] = field(default_factory=list)
    device: dict | None = None  # {from, to, text}
    forecasts: list[dict] = field(default_factory=list)

    @property
    def peak_rank(self) -> int:
        return TIER_RANK.get((self.peak or {}).get("tier") or "STABLE", 0)

    @property
    def flagged(self) -> bool:
        concern = any(n.get("concern") in ("med", "high") for n in self.notes)
        return self.peak_rank >= TIER_RANK["HIGH"] or bool(self.alerts or self.falls or self.device) or concern

    @property
    def label(self) -> str:
        return f"{self.name} ({self.room})"


@dataclass
class ShiftData:
    shift: str
    day: date
    start: datetime
    end: datetime
    complete: bool
    residents: dict[int, ResidentShift]
    total_checks: int
    source: str  # replay_snapshots | priority_snapshots | mixed

    @property
    def flagged(self) -> list[ResidentShift]:
        return sorted((r for r in self.residents.values() if r.flagged),
                      key=lambda r: (-r.peak_rank, -((r.peak or {}).get("score") or 0)))  # fmt: skip

    @property
    def watch_only(self) -> list[ResidentShift]:
        return [r for r in self.residents.values() if not r.flagged and r.peak_rank >= TIER_RANK["WATCH"]]


SNAPSHOTS_SQL = """
    WITH s AS (
        SELECT ts, resident_id, score, tier, factors FROM replay_snapshots
        WHERE ts >= %(rs)s AND ts < %(re)s
        UNION ALL
        SELECT ts, resident_id, score, tier, factors FROM priority_snapshots
        WHERE ts >= %(start)s AND ts < %(end)s AND NOT (ts >= %(rs)s AND ts < %(re)s)
    ), c AS (
        SELECT *, lag(tier) OVER (PARTITION BY resident_id ORDER BY ts) AS prev,
               row_number() OVER (PARTITION BY resident_id ORDER BY
                   CASE tier WHEN 'CRITICAL' THEN 3 WHEN 'HIGH' THEN 2 WHEN 'WATCH' THEN 1 ELSE 0 END DESC,
                   score DESC, ts) AS peak_rn,
               row_number() OVER (PARTITION BY resident_id ORDER BY ts DESC) AS last_rn
        FROM s
    )
    SELECT resident_id, ts, tier, score, factors, prev IS NULL AS first, peak_rn = 1 AS peak, last_rn = 1 AS last,
           factors @> '[{"kind": "device"}]' AS device
    FROM c
    WHERE prev IS DISTINCT FROM tier OR peak_rn = 1 OR last_rn = 1 OR factors @> '[{"kind": "device"}]'
    ORDER BY resident_id, ts
"""


async def gather(conn: psycopg.AsyncConnection, shift: str, day: date, tz: ZoneInfo) -> ShiftData:
    start, end = shift_window(shift, day, tz)
    now = datetime.now(UTC)
    complete = end <= now
    end = min(end, now)
    if end <= start:
        raise ValueError("that shift has not started yet")
    prepared = await load_prepared(conn)
    rs = re_ = start  # the prepared replay night's span inside this shift (empty if none)
    if prepared:
        p0, p1 = datetime.fromisoformat(prepared["anchor"]), datetime.fromisoformat(prepared["end"])
        if p0 < end and p1 > start:
            rs, re_ = max(p0, start), min(p1, end)
    roster = await tiger.fetch_dicts(
        conn, "SELECT resident_id, name, room, unit, age, sex, code_status, cognition, diagnoses, flags FROM residents"
    )
    res = {r["resident_id"]: ResidentShift(r["resident_id"], r["name"], r["room"], r["unit"], r["age"], r["sex"],
                                           r["code_status"], r["cognition"], list(r["diagnoses"] or []))
           for r in roster}  # fmt: skip

    for r in await tiger.fetch_dicts(conn, SNAPSHOTS_SQL, {"start": start, "end": end, "rs": rs, "re": re_}):
        x = res.get(r["resident_id"])
        if x is None:
            continue
        point = {"ts": r["ts"], "tier": r["tier"], "score": round(r["score"])}
        if r["first"]:
            x.start_tier, x.start_score = r["tier"], point["score"]
        elif r["tier"] != (x.changes[-1]["tier"] if x.changes else x.start_tier):
            x.changes.append(point)
        if r["peak"]:
            # the device factor's text ("off for 10 min") is a snapshot; the device issue's full span is added below
            x.peak = point | {"factors": [f.get("text") for f in r["factors"] or [] if f.get("kind") != "device"]}
        if r["last"]:
            x.end_tier, x.end_score = r["tier"], point["score"]
        if r["device"]:
            text = next((f.get("text") for f in r["factors"] or [] if f.get("kind") == "device"), "device issue")
            if x.device is None:
                x.device = {"from": r["ts"], "to": r["ts"], "text": text}
            else:
                x.device["to"] = r["ts"]

    if prepared and rs < re_:  # the replay night: alerts as the prepare pass raised them
        for a in prepared.get("alerts") or []:
            ts = datetime.fromisoformat(a["ts"])
            if rs <= ts < re_ and (x := res.get(int(a["resident_id"]))):
                x.alerts.append(a | {"ts": ts})
    events = await tiger.fetch_dicts(
        conn,
        """SELECT ts, resident_id, type, payload FROM events
           WHERE ts >= %s AND ts < %s AND type IN ('alert_raised', 'alert_resolved', 'check_logged', 'note_added',
                 'fall_suspected', 'bed_exit', 'device_offline', 'device_online')
           ORDER BY ts""",
        (start, end),
    )
    extracts = {
        (r["resident_id"], r["note_ts"]): r["payload"]
        for r in await tiger.fetch_dicts(
            conn,
            """SELECT resident_id, payload->>'note_ts' AS note_ts, payload FROM ai_insights
               WHERE kind = 'note_extract' AND as_of >= %s AND as_of < %s + INTERVAL '1 hour'""",
            (start, end),
        )
    }
    total_checks = 0
    for e in events:
        x = res.get(e["resident_id"])
        if x is None:
            continue
        p, t = e["payload"] or {}, e["type"]
        if t == "alert_raised" and not (rs <= e["ts"] < re_):
            x.alerts.append({"ts": e["ts"], "tier": p.get("tier"), "from_tier": p.get("from_tier"),
                             "score": p.get("score"), "rule": p.get("rule"), "factors": p.get("factors") or []})  # fmt: skip
        elif t == "alert_resolved":
            x.resolved.append({"ts": e["ts"], "tier": p.get("tier"), "reason": p.get("reason")})
        elif t == "check_logged":
            total_checks += 1
            x.checks += 1
            x.last_check = e["ts"]
        elif t == "note_added" and not p.get("routine"):
            ex = extracts.get((x.resident_id, e["ts"].isoformat())) or {}
            x.notes.append({"ts": e["ts"], "role": p.get("author_role") or "staff", "text": p.get("text") or "",
                            "concern": ex.get("concern_level"), "confusion": ex.get("confusion_change")})  # fmt: skip
        elif t == "fall_suspected":
            x.falls.append(e["ts"])
        elif t == "bed_exit":
            x.bed_exits += 1
        elif t == "device_offline" and x.device is None:
            x.device = {"from": e["ts"], "to": e["ts"], "text": "device offline"}
        elif t == "device_online" and x.device is not None:
            x.device["to"] = e["ts"]
    for x in res.values():  # the prepare pass's falls are alerts with rule = fall
        for a in x.alerts:
            if a.get("rule") == "fall" and not any(abs((a["ts"] - f).total_seconds()) < 600 for f in x.falls):
                x.falls.append(a["ts"])
    copd = {r["resident_id"] for r in roster if "copd" in (r.get("flags") or ())}
    for r in await tiger.fetch_dicts(
        conn,
        """SELECT resident_id, as_of, payload FROM ai_insights
           WHERE kind = 'forecast' AND as_of >= %s AND as_of < %s ORDER BY as_of""",
        (start, end),
    ):
        if (x := res.get(r["resident_id"])) and (
            c := trend.annotate(r["payload"], tz, x.resident_id in copd)["callout"]
        ):
            x.forecasts.append({"ts": r["as_of"], "callout": c})
    for x in res.values():
        if x.device and x.device["to"] - x.device["from"] < DEVICE_MIN:
            x.device = None
        elif x.device:
            mins = round((x.device["to"] - x.device["from"]).total_seconds() / 60)
            what = "Wristband not worn" if "off" in x.device["text"].lower() else x.device["text"].capitalize()
            x.device["text"] = (f"{what} {_hm(x.device['from'], tz)}–{_hm(x.device['to'], tz)} "
                                f"({mins // 60} h {mins % 60:02d} min)")  # fmt: skip
    source = "replay_snapshots" if rs <= start and re_ >= end else "priority_snapshots" if rs == re_ else "mixed"
    return ShiftData(shift, day, start, end, complete, res, total_checks, source)


# --------------------------------------------------------------------------- rows for AI_AGG


def _hm(ts: datetime | str | None, tz: ZoneInfo) -> str:
    if ts is None:
        return "—"
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.astimezone(tz).strftime("%H:%M")


def resident_row(x: ResidentShift, d: ShiftData, tz: ZoneInfo) -> str:
    parts = [
        f"RESIDENT R-{x.resident_id} {x.name}, {x.age}{x.sex}, {x.unit} room {x.room}, code status {x.code_status}, "
        f"baseline cognition {x.cognition}. Diagnoses: {'; '.join(x.diagnoses[:4]) or 'none listed'}.",
        f"Tier at shift start ({_hm(d.start, tz)}): {x.start_tier or '—'} {x.start_score if x.start_score is not None else ''}.",
    ]
    if x.changes:
        parts.append(
            "Tier changes: " + "; ".join(f"{_hm(c['ts'], tz)} {c['tier']} {c['score']}" for c in x.changes) + "."
        )
    if x.peak:
        parts.append(f"Peak: {x.peak['tier']} {x.peak['score']} at {_hm(x.peak['ts'], tz)} "
                     f"({'; '.join(f for f in x.peak['factors'] if f) or 'no factors'}).")  # fmt: skip
    parts.append(
        f"At shift end ({_hm(d.end, tz)}): {x.end_tier or '—'} {x.end_score if x.end_score is not None else ''}."
    )
    if x.alerts:
        parts.append("Vigil alerts: " + "; ".join(
            f"{_hm(a['ts'], tz)} {a.get('from_tier') or '?'}→{a.get('tier')}{' (' + a['rule'] + ')' if a.get('rule') else ''}"
            for a in x.alerts) + ".")  # fmt: skip
    if x.resolved:
        parts.append(
            "Resolved: " + "; ".join(f"{_hm(r['ts'], tz)} ({r.get('reason') or 'improved'})" for r in x.resolved) + "."
        )
    if x.falls:
        parts.append("Suspected fall: " + ", ".join(_hm(t, tz) for t in x.falls) + ".")
    parts.append(f"Checks logged: {x.checks}{f', last {_hm(x.last_check, tz)}' if x.last_check else ''}. "
                 f"Bed exits: {x.bed_exits}.")  # fmt: skip
    for n in x.notes:
        tag = f" [AI_EXTRACT: concern {n['concern']}, confusion {n['confusion']}]" if n.get("concern") else ""
        parts.append(f'Note {_hm(n["ts"], tz)} {n["role"]}: "{n["text"]}"{tag}.')
    for f in x.forecasts[-2:]:
        parts.append(f"Snowflake forecast {_hm(f['ts'], tz)}: {f['callout']}.")
    if x.device:
        parts.append(f"Device issue {_hm(x.device['from'], tz)}–{_hm(x.device['to'], tz)}: {x.device['text']}.")
    return " ".join(parts)


def event_rows(x: ResidentShift, d: ShiftData, tz: ZoneInfo) -> list[str]:
    """One resident's shift as separate records (the rows AI_AGG aggregates per resident)."""
    rows = [f"Profile: {x.age}{x.sex}, {x.unit} room {x.room}, code status {x.code_status}, baseline cognition "
            f"{x.cognition}. Diagnoses: {'; '.join(x.diagnoses[:4]) or 'none listed'}.",
            f"Tier at shift start ({_hm(d.start, tz)}): {x.start_tier or '—'} {x.start_score if x.start_score is not None else ''}; "
            f"at shift end ({_hm(d.end, tz)}): {x.end_tier or '—'} {x.end_score if x.end_score is not None else ''}."]  # fmt: skip
    if x.peak:
        rows.append(f"Peak {x.peak['tier']} {x.peak['score']} at {_hm(x.peak['ts'], tz)}: "
                    f"{'; '.join(f for f in x.peak['factors'] if f) or 'no factors'}.")  # fmt: skip
    rows += [f"{_hm(c['ts'], tz)} tier changed to {c['tier']} (score {c['score']})." for c in x.changes]
    rows += [f"{_hm(a['ts'], tz)} Vigil alert {a.get('from_tier') or '?'}→{a.get('tier')}"
             f"{' (' + str(a['rule']).replace('_', ' ') + ')' if a.get('rule') else ''}: {'; '.join(a.get('factors') or [])}."
             for a in x.alerts]  # fmt: skip
    rows += [f"{_hm(t, tz)} suspected fall (impact, then no movement)." for t in x.falls]
    rows.append(f"Checks logged this shift: {x.checks}{f', last {_hm(x.last_check, tz)}' if x.last_check else ''}. "
                f"Bed exits: {x.bed_exits}.")  # fmt: skip
    for n in x.notes:
        tag = f" (AI_EXTRACT: concern {n['concern']}, confusion {n['confusion']})" if n.get("concern") else ""
        rows.append(f'{_hm(n["ts"], tz)} note by {n["role"]}: "{n["text"]}"{tag}.')
    rows += [f"{_hm(f['ts'], tz)} Snowflake forecast: {f['callout']}." for f in x.forecasts[-2:]]
    if x.device:
        rows.append(f"Device issue: {x.device['text']}.")
    return rows


def shift_rows(d: ShiftData, tz: ZoneInfo) -> list[str]:
    flagged = d.flagged
    alerts = sum(len(x.alerts) for x in d.residents.values())
    with_alerts = sum(1 for x in d.residents.values() if x.alerts)
    head = (
        f"SHIFT: {d.shift} shift {_hm(d.start, tz)}–{_hm(d.end, tz)}{'' if d.complete else ' (in progress)'}, "
        f"starting {d.start.astimezone(tz):%a %d %b}, Sunrise Gardens Skilled Nursing Facility, {len(d.residents)} "
        f"residents. Checks logged: {d.total_checks}. Vigil alerts: {alerts} for {with_alerts} residents. "
        f"Residents needing attention: {len(flagged)}."
    )
    rows = [head] + [resident_row(x, d, tz) for x in flagged[:MAX_DETAILED]]
    watch = d.watch_only
    if watch:
        rows.append("ALSO WATCH at some point this shift (stable now unless noted): " + "; ".join(
            f"{x.label} peak {x.peak['score']} at {_hm(x.peak['ts'], tz)}, end {x.end_tier}" for x in watch[:20]))  # fmt: skip
    return rows


# --------------------------------------------------------------------------- deterministic handoff


def sbar_line(x: ResidentShift, tz: ZoneInfo) -> dict:
    peak = x.peak or {}
    situation = (f"Peak {peak.get('tier', '—')} {peak.get('score', '')} at {_hm(peak.get('ts'), tz)}; "
                 f"{x.end_tier or '—'} at shift end.")  # fmt: skip
    if x.falls:
        situation = f"Suspected fall at {_hm(x.falls[0], tz)}. " + situation
    if x.device and peak.get("tier") in (None, "STABLE", "WATCH") and not x.alerts:
        situation = f"Wristband/device issue {_hm(x.device['from'], tz)}–{_hm(x.device['to'], tz)}."
    background = f"{x.age}{x.sex}, {'; '.join(x.diagnoses[:2]) or 'no diagnoses listed'}; code status {x.code_status}."
    factors = [f for f in peak.get("factors") or [] if f]
    notes = [n for n in x.notes if n.get("concern") in ("med", "high")]
    assessment = "; ".join(factors[:3]) or (x.device or {}).get("text") or "No notable signals."
    if notes:
        assessment += f". Note {_hm(notes[-1]['ts'], tz)}: {notes[-1]['text'][:90]}"
    tier = x.end_tier or "STABLE"
    action = TIER_HEADLINE.get(tier, "Routine rounds")
    if x.device and tier in ("STABLE", "WATCH"):
        action = "Check the wristband is on and charged"
    recommendation = f"{action}. {CODE_STATUS_ESCALATION.get(x.code_status, '')}".strip()
    return {"resident_id": x.resident_id, "name": x.name, "room": x.room, "situation": situation,
            "background": background, "assessment": assessment, "recommendation": recommendation}  # fmt: skip


def deterministic(d: ShiftData, tz: ZoneInfo) -> dict:
    flagged = d.flagged
    crit = sorted(((a["ts"], x, a) for x in d.residents.values() for a in x.alerts if a.get("tier") == "CRITICAL"),
                  key=lambda t: t[0])  # fmt: skip
    critical = [{"time": _hm(ts, tz), "resident": x.label,
                 "text": f"CRITICAL ({(a.get('rule') or 'score').replace('_', ' ')}): "
                         f"{'; '.join(a.get('factors') or [])[:140]}"} for ts, x, a in crit]  # fmt: skip
    watch = [{"resident": x.label, "reason": "; ".join((x.peak or {}).get("factors") or [])[:140] or "flagged",
              "action": TIER_HEADLINE.get(x.end_tier or "STABLE", "")}
             for x in sorted(d.residents.values(), key=lambda r: -TIER_RANK.get(r.end_tier or "STABLE", 0))
             if TIER_RANK.get(x.end_tier or "STABLE", 0) >= TIER_RANK["WATCH"]][:12]  # fmt: skip
    resolved = [{"resident": x.label, "text": f"{len(x.resolved)} alert(s) resolved, last {_hm(x.resolved[-1]['ts'], tz)} "
                 f"({x.resolved[-1].get('reason') or 'improved'})"} for x in flagged if x.resolved]  # fmt: skip
    if not resolved:
        resolved = [{"resident": x.label, "text": f"Was {x.peak['tier']} at {_hm(x.peak['ts'], tz)}; {x.end_tier} at end"}
                    for x in flagged if x.peak and x.peak_rank >= TIER_RANK["HIGH"]
                    and TIER_RANK.get(x.end_tier or "STABLE", 0) < TIER_RANK["HIGH"]]  # fmt: skip
    devices = [{"resident": x.label, "text": x.device["text"]} for x in d.residents.values() if x.device]
    follow = [f"{x.label}: {n['text'][:140]}" for x in flagged for n in x.notes if n["role"] == "RN"]
    return {"critical_events": critical, "watch_next_shift": watch, "resolved": resolved, "device_issues": devices,
            "follow_ups": follow[:8], "residents": [sbar_line(x, tz) for x in flagged[:MAX_DETAILED]]}  # fmt: skip


# --------------------------------------------------------------------------- validate AI_AGG output

SECTIONS = ("critical_events", "watch_next_shift", "resolved", "device_issues", "follow_ups")
SBAR_KEYS = ("situation", "background", "assessment", "recommendation")
_TEXT_KEYS = ("text", "reason", "action", "situation", "background", "assessment", "recommendation")


_HEDGED_KEYS = {"assessment", "reason", "text"}  # claims about now; background/profile text is never hedged


def _clean(v, limit: int = 300, *, hedge: bool = True) -> str:
    s = " ".join(str(v or "").split())
    return soften(s if len(s) <= limit else s[: limit - 1] + "…", hedge=hedge)


def validate(obj: dict, sbar: dict[int, dict | None], d: ShiftData, tz: ZoneInfo) -> tuple[dict, list[str]]:
    """AI_AGG's JSON → the handoff shape. `sbar`: resident_id → AI_AGG's SBAR object (GROUP BY resident). Every
    flagged resident gets an SBAR entry (the deterministic one if AI_AGG gave none). → (handoff, what was fixed)."""
    if not isinstance(obj, dict):
        raise ValueError("AI_AGG did not return a JSON object")
    fixes: list[str] = []
    out: dict = {}
    for key in SECTIONS:
        items = obj.get(key) or []
        if not isinstance(items, list):
            items = []
        if key == "follow_ups":
            out[key] = [_clean(x, hedge=False) for x in items if isinstance(x, str | int | float) and str(x).strip()][
                :10
            ]
            continue
        clean = []
        for it in items:
            if not isinstance(it, dict):
                continue
            clean.append({k: (_clean(v, hedge=k in _HEDGED_KEYS) if k in _TEXT_KEYS else " ".join(str(v).split())[:80] if isinstance(v, str) else v)
                          for k, v in it.items()})  # fmt: skip
        out[key] = clean[:20]
    residents = []
    for x in d.flagged[:MAX_DETAILED]:
        r = sbar.get(x.resident_id)
        r = {k: _clean(r.get(k), hedge=k in _HEDGED_KEYS) for k in SBAR_KEYS} if isinstance(r, dict) else None
        if r is None or not all(r.values()):
            fixes.append(f"R-{x.resident_id}: deterministic SBAR (AI_AGG gave none)")
            r = sbar_line(x, tz) | {"source": "template"}
        residents.append(r | {"resident_id": x.resident_id, "name": x.name, "room": x.room})
    out["residents"] = residents
    texts = [str(v) for sec in (*SECTIONS, "residents") for it in out[sec]
             for v in (it.values() if isinstance(it, dict) else [it])]  # fmt: skip
    if bad := sorted({v for t in texts for v in violations(t)}):
        raise ValueError(f"firm diagnostic wording ({', '.join(bad)})")
    return out, fixes


# --------------------------------------------------------------------------- generate / store / read


_cache: dict[tuple[str, str], dict] = {}


async def ai_agg(rows: list[str], *, timeout: float = AGG_TIMEOUT_S) -> str:
    """The shift's sections: AI_AGG over every row (the SHIFT row + one RESIDENT row each)."""
    result = await rest_client().sql(
        "SELECT AI_AGG(value::VARCHAR, ?) FROM TABLE(FLATTEN(INPUT => PARSE_JSON(?)))",
        [INSTRUCTION, json.dumps(rows)],
        feature="sql_api.handoff_agg",
        timeout=timeout,
    )
    return str(result[0][0] or "")


async def ai_agg_sbar(records: list[dict], *, timeout: float = AGG_TIMEOUT_S) -> dict[int, dict | None]:
    """One SBAR per resident: AI_AGG over that resident's event rows, GROUP BY resident. → rid → parsed JSON."""
    result = await rest_client().sql(
        "SELECT value:r::NUMBER AS rid, AI_AGG(value:t::VARCHAR, ?) FROM TABLE(FLATTEN(INPUT => PARSE_JSON(?))) "
        "GROUP BY rid",
        [SBAR_INSTRUCTION, json.dumps(records)],
        feature="sql_api.handoff_sbar",
        timeout=timeout,
    )
    out: dict[int, dict | None] = {}
    for rid, text in result:
        try:
            out[int(rid)] = parse_json_text(str(text or ""))
        except ValueError:
            out[int(rid)] = None
    return out


async def generate(conn: psycopg.AsyncConnection, shift: str, day: date, tz: ZoneInfo) -> dict:
    t0 = time.perf_counter()
    d = await gather(conn, shift, day, tz)
    rows = shift_rows(d, tz)
    records = [{"r": x.resident_id, "t": t} for x in d.flagged[:MAX_DETAILED] for t in event_rows(x, d, tz)]
    base = {
        "shift": shift, "date": day.isoformat(), "start": d.start.isoformat(), "end": d.end.isoformat(),
        "complete": d.complete, "source": d.source, "rows": len(rows), "residents_flagged": len(d.flagged),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "flagged": [{"resident_id": x.resident_id, "name": x.name, "room": x.room,
                     "peak_tier": (x.peak or {}).get("tier"), "end_tier": x.end_tier} for x in d.flagged],
        "input_rows": rows,
        "sbar_rows": len(records),
    }  # fmt: skip
    try:
        text, sbar = await asyncio.gather(ai_agg(rows), ai_agg_sbar(records))
        handoff, fixes = validate(parse_json_text(text), sbar, d, tz)
        # Device issues are plain facts (who, from when to when): listed from the data, not paraphrased by AI_AGG.
        handoff["device_issues"] = deterministic(d, tz)["device_issues"]
        result = base | handoff | {"model": MODEL, "via": "sql_api", "fixes": fixes, "source_text_chars": len(text)}
    except Exception as exc:
        log.warning("handoff: AI_AGG failed, using the deterministic handoff: %s", " ".join(str(exc).split())[:200])
        reason = " ".join(str(exc).split())[:160] or type(exc).__name__
        result = base | deterministic(d, tz) | {"model": "deterministic template", "fallback_reason": reason}
    result["ms"] = round((time.perf_counter() - t0) * 1000)
    _cache[(day.isoformat(), shift)] = result
    if result["model"] == MODEL:
        await _store(day, shift, result)
    return result


async def _store(day: date, shift: str, payload: dict) -> None:
    try:
        await rest_client().sql(
            "INSERT INTO CORE.HANDOFFS (SHIFT_DATE, SHIFT, MODEL, PAYLOAD, CREATED_AT) "
            "SELECT ?::DATE, ?, ?, PARSE_JSON(?), SYSDATE()",
            [day.isoformat(), shift, payload["model"], json.dumps(payload, default=str)],
            feature="sql_api.store_handoff",
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 — the page has it; Snowflake is the durable copy
        log.warning("handoff: storing in CORE.HANDOFFS failed: %s", " ".join(str(exc).split())[:160])


async def cached(shift: str, day: date) -> dict | None:
    """This process's latest handoff for the shift, else the newest stored in CORE.HANDOFFS."""
    key = (day.isoformat(), shift)
    if key in _cache:
        return _cache[key]
    try:
        rows = await rest_client().sql(
            "SELECT PAYLOAD FROM CORE.HANDOFFS WHERE SHIFT_DATE = ?::DATE AND SHIFT = ? ORDER BY CREATED_AT DESC LIMIT 1",
            [day.isoformat(), shift],
            feature="sql_api.read_handoff",
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("handoff: reading CORE.HANDOFFS failed: %s", exc)
        return None
    if not rows:
        return None
    payload = json.loads(rows[0][0])
    _cache[key] = payload
    return payload
