"""Outcome metrics for the replay night (SPEC §13): fixed-threshold alarms vs Vigil alerts, and Alvarez's lead time.

Fixed-threshold alarms are counted now, with SQL over last night's raw readings and bed exits
(`tiger.fixed_threshold_alarms`). Vigil's side comes from the prepare step's gated pass: the alerts the real
evaluator and alert gate raised as_of each replay step, and each resident's tier changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.db import tiger
from app.scoring.priority import TIER_RANK
from app.sim import facility as fac
from app.sim.cast import CAST_IDS
from app.sim.replay import Night, load_night, load_prepared

MERGE = timedelta(minutes=5)  # abnormal readings closer than this are one fixed-threshold alarm
VITAL_KINDS = ("hr_high", "hr_low", "rr_high", "rr_low", "spo2_low")
KIND_TEXT = {
    "hr_high": "HR > 100",
    "hr_low": "HR < 50",
    "rr_high": "RR > 24",
    "rr_low": "RR < 10",
    "spo2_low": "SpO₂ < 90",
    "bed_exit": "Any bed exit",
}
ALVAREZ = 17


def _ts(v: str | datetime) -> datetime:
    return (v if isinstance(v, datetime) else datetime.fromisoformat(v)).astimezone(UTC)


def first_reaching(transitions: list[list], tier: str, since: datetime) -> datetime | None:
    """First tier change at or after `since` into `tier` or above ([[ts, tier, score], …])."""
    for ts, t, *_ in transitions:
        if _ts(ts) >= since and TIER_RANK[t] >= TIER_RANK[tier]:
            return _ts(ts)
    return None


def first_number_one(cast_rows: list[list], since: datetime, tier: str = "HIGH") -> datetime | None:
    """First time at or after `since` the resident is #1 in the queue at `tier` or above ([[ts, tier, score, rank]])."""
    for ts, t, _score, rank in cast_rows:
        if _ts(ts) >= since and rank == 1 and TIER_RANK[t] >= TIER_RANK[tier]:
            return _ts(ts)
    return None


def next_round(after: datetime, tz: ZoneInfo) -> datetime:
    """The first scheduled night round (22:00, 00:00, 02:00, 04:00, 06:00 facility time) at or after `after`."""
    local = after.astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [
        (day + timedelta(days=d)).replace(hour=h)
        for d in (0, 1)
        for h in sorted(fac.NIGHT_ROUND_HOURS)
    ]  # fmt: skip
    return min(c for c in candidates if c >= local).astimezone(UTC)


def minutes(a: datetime | None, b: datetime | None) -> float | None:
    """b − a in minutes (positive: a came first)."""
    return None if a is None or b is None else round((b - a).total_seconds() / 60, 1)


def summarize_fixed(rows: list[dict]) -> dict:
    by_kind = {k: 0 for k in KIND_TEXT}
    residents: set[int] = set()
    for r in rows:
        by_kind[r["kind"]] += int(r["alarms"])
        residents.add(r["resident_id"])
    vitals = sum(by_kind[k] for k in VITAL_KINDS)
    return {
        "total": sum(by_kind.values()),
        "vital_signs": vitals,
        "bed_exits": by_kind["bed_exit"],
        "residents": len(residents),
        "by_kind": [{"kind": k, "label": KIND_TEXT[k], "alarms": n} for k, n in by_kind.items()],
    }


def summarize_vigil(alerts: list[dict]) -> dict:
    by_tier = {"CRITICAL": 0, "HIGH": 0}
    for a in alerts:
        by_tier[a["tier"]] = by_tier.get(a["tier"], 0) + 1
    return {
        "total": len(alerts),
        "residents": len({a["resident_id"] for a in alerts}),
        "by_tier": by_tier,
        "rule_alerts": sum(1 for a in alerts if a.get("new_rules")),
        "score_alerts": sum(1 for a in alerts if not a.get("new_rules")),
    }


def alvarez_timeline(prepared: dict, fixed_rows: list[dict], checks: list[dict], notes: list[dict],
                     night: Night, tz: ZoneInfo) -> dict:  # fmt: skip
    trans = prepared.get("transitions", {}).get(str(ALVAREZ), [])
    cast = prepared.get("cast", {}).get(str(ALVAREZ), [])
    watch = first_reaching(trans, "WATCH", night.start)
    high = first_reaching(trans, "HIGH", night.start)
    top = first_number_one(cast, night.start)
    mine = [r for r in fixed_rows if r["resident_id"] == ALVAREZ]
    vital = [r for r in mine if r["kind"] in VITAL_KINDS]
    first_vital = min((r["first_ts"] for r in vital), default=None)
    first_vital_kind = next((KIND_TEXT[r["kind"]] for r in vital if r["first_ts"] == first_vital), None)
    first_any = min((r["first_ts"] for r in mine), default=None)
    round_at = next_round(high, tz) if high else None
    # Her round check: logged as that round's check (a round walks the unit from 20 min before the hour).
    early = timedelta(minutes=fac.ROUND_SPREAD_MIN / 2)
    checked = next(
        (c["ts"] for c in checks
         if round_at and c["ts"] >= round_at - early and (c.get("payload") or {}).get("kind") == "round"),
        None,
    )  # fmt: skip
    return {
        "resident_id": ALVAREZ,
        "vigil_watch": watch,
        "vigil_high": high,
        "vigil_number_one": top,
        "scheduled_round": round_at,
        "round_check": checked,
        "first_fixed_vital": first_vital,
        "first_fixed_vital_kind": first_vital_kind,
        "first_fixed_any": first_any,
        "fixed_alarms": sum(int(r["alarms"]) for r in mine),
        "fixed_bed_exit_alarms": sum(int(r["alarms"]) for r in mine if r["kind"] == "bed_exit"),
        "lead_vs_round_min": minutes(high, round_at),
        "lead_vs_fixed_min": minutes(high, first_vital),
        "alerts": [
            {"ts": a["ts"], "tier": a["tier"], "from_tier": a["from_tier"], "score": a["score"], "rank": a["rank"]}
            for a in prepared.get("alerts", [])
            if a["resident_id"] == ALVAREZ and night.start <= _ts(a["ts"]) < night.end
        ],
        "notes": notes,
    }


_fixed_cache: dict[tuple, list[dict]] = {}  # (seed, night start) → rows: the recorded night never changes


async def outcome(conn: psycopg.AsyncConnection, tz: ZoneInfo) -> dict:
    night = await load_night(conn)
    prepared = await load_prepared(conn)
    if night is None or prepared is None:
        return {"prepared": False, "message": "Run Prepare replay first: Vigil's side comes from its gated pass."}
    key = (prepared.get("seeded_at"), night.start)
    fixed = _fixed_cache.get(key)
    if fixed is None:
        fixed = await tiger.fixed_threshold_alarms(conn, night.start, night.end, MERGE)
        _fixed_cache.clear()
        _fixed_cache[key] = fixed
    events = await tiger.fetch_dicts(
        conn,
        """SELECT ts, type, payload FROM events
           WHERE resident_id = %s AND type IN ('check_logged', 'note_added') AND ts >= %s AND ts < %s ORDER BY ts""",
        (ALVAREZ, night.start, night.end),
    )
    checks = [e for e in events if e["type"] == "check_logged"]
    notes = [{"ts": e["ts"], "text": (e["payload"] or {}).get("text", "")} for e in events if e["type"] == "note_added"]
    alerts = [a for a in prepared.get("alerts", []) if night.start <= _ts(a["ts"]) < night.end]
    per_cast = []
    for rid in CAST_IDS:
        per_cast.append({
            "resident_id": rid,
            "fixed": sum(int(r["alarms"]) for r in fixed if r["resident_id"] == rid),
            "vigil": sum(1 for a in alerts if a["resident_id"] == rid),
        })  # fmt: skip
    return {
        "prepared": True,
        "night": {"date": night.date, "start": night.start, "end": night.end},
        "rules": {"merge_minutes": MERGE.total_seconds() / 60, "fixed": list(KIND_TEXT.values())},
        "fixed": summarize_fixed(fixed),
        "vigil": summarize_vigil(alerts),
        "cast": per_cast,
        "alvarez": alvarez_timeline(prepared, fixed, checks, notes, night, tz),
    }
