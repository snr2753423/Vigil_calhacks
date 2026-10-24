"""Residents: roster, detail, and chart series (SPEC §11.2, §12)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import TigerConn, facility_tz, parse_ts
from app.db import tiger
from app.scoring import evaluator as ev
from app.scoring.baseline import Baselines, tod_block

router = APIRouter(prefix="/api/residents", tags=["residents"])

DEFAULT_RANGE = timedelta(hours=6)
MAX_POINTS = 20_000
RES_STEP = {"1m": timedelta(minutes=1), "15m": timedelta(minutes=15), "1h": timedelta(hours=1)}


def _current(rid: int) -> dict | None:
    """This resident's entry in the live evaluator's latest queue."""
    live = ev.evaluator
    if live is None or live.latest is None:
        return None
    for rank, it in enumerate(live.latest.items, start=1):
        if it.profile.resident_id == rid:
            return it.to_dict(rank)
    return None


@router.get("")
async def list_residents(conn: TigerConn) -> list[dict]:
    rows = await tiger.fetch_dicts(
        conn,
        """SELECT resident_id, name, age, sex, room, unit, diagnoses, flags, morse, mobility, cognition,
                  code_status, is_live
           FROM residents ORDER BY resident_id""",
    )
    for r in rows:
        cur = _current(r["resident_id"])
        r["current"] = None if cur is None else {k: cur[k] for k in ("rank", "score", "tier", "factors", "rule")}
    return rows


@router.get("/{resident_id}")
async def get_resident(resident_id: int, conn: TigerConn, as_of: str | None = None) -> dict:
    """Profile, baselines, current score and the last 24 h of events. Without `as_of`: live (the evaluator's
    latest entry). With `as_of`: everything as of that time (replay cursor), scored statelessly."""
    rows = await tiger.fetch_dicts(conn, "SELECT * FROM residents WHERE resident_id = %s", (resident_id,))
    if not rows:
        raise HTTPException(404, f"no resident {resident_id}")
    resident = rows[0]
    ts = parse_ts(as_of)
    at = ts or datetime.now(UTC)
    live = ev.evaluator
    baselines = live.ctx.baselines if live is not None and live.ctx is not None else None
    if baselines is None:
        baselines = Baselines(await tiger.load_baseline_rows(conn))
    behavior = baselines.behavior_for(resident_id)
    current = _current(resident_id) if ts is None else None
    if current is None:  # historical as_of, or the evaluator isn't running: score the facility once
        queue = await ev.evaluate_at(conn, at, facility_tz(), live.ctx if live else None, live=ts is None)
        current = next(
            (it.to_dict(i) for i, it in enumerate(queue.items, 1) if it.profile.resident_id == resident_id), None
        )
    events = await tiger.fetch_dicts(
        conn,
        """SELECT ts, type, staff_id, payload FROM events
           WHERE resident_id = %(rid)s AND ts > %(as_of)s - INTERVAL '24 hours' AND ts <= %(as_of)s
           ORDER BY ts DESC LIMIT 200""",
        {"rid": resident_id, "as_of": at},
    )
    return {
        **resident,
        "as_of": at.isoformat(),
        "baselines": {
            "tod_block_now": tod_block(at, facility_tz()),
            "vitals": baselines.block_stats(resident_id),
            "night_bed_exits_p95": behavior.night_bed_exits_p95,
            "still_minutes_p95": behavior.still_minutes_p95,
        },
        "current": current,
        "events": events,
    }


@router.get("/{resident_id}/vitals")
async def get_vitals(
    resident_id: int,
    conn: TigerConn,
    metrics: str = Query("hr,rr,spo2,skin_temp,motion,in_bed", description="comma-separated"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    res: str | None = Query(None, pattern="^(1m|15m|1h)$"),
) -> dict:
    """Gap-filled series (time_bucket_gapfill + locf) from vitals_1m / vitals_15m / vitals_1h. Default: the
    last 6 h; resolution defaults to 1m up to 12 h, 15m up to 4 days, else 1h."""
    names = [m.strip() for m in metrics.split(",") if m.strip()]
    unknown = [m for m in names if m not in tiger.SERIES_METRICS]
    if not names or unknown:
        raise HTTPException(400, f"unknown metrics {unknown}; choose from {', '.join(tiger.SERIES_METRICS)}")
    end = parse_ts(to, "to") or datetime.now(UTC)
    start = parse_ts(from_, "from") or end - DEFAULT_RANGE
    if start >= end:
        raise HTTPException(400, "from must be before to")
    span = end - start
    res = res or ("1m" if span <= timedelta(hours=12) else "15m" if span <= timedelta(days=4) else "1h")
    if span / RES_STEP[res] > MAX_POINTS:
        raise HTTPException(400, f"{span} at {res} is more than {MAX_POINTS} points; use a coarser res")
    rows = await tiger.vitals_series(conn, resident_id, names, start, end, res)
    return {
        "resident_id": resident_id,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "res": res,
        "ts": [r["ts"].isoformat() for r in rows],
        "series": {m: [r[m] for r in rows] for m in names},
    }
