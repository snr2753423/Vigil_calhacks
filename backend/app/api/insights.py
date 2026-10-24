"""GET /api/insights/{id}: cached Snowflake deep-tier results from Tiger ai_insights (SPEC §12).

The UI never waits on Snowflake: everything here was written by the sync loop / `make train` / `make detect`.
kind=anomaly returns one entry per closed 15-min bucket with each metric's value, the model's expected
value and 99 % interval, and Vigil's severity (chart overlay: expected band + anomaly dots).
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import TigerConn, parse_ts
from app.db import tiger
from app.deep.anomaly import REVEAL_DELAY
from app.deep.sync import BUCKET

router = APIRouter(prefix="/api/insights", tags=["insights"])

KINDS = ("anomaly", "forecast", "assessment", "note_extract")
DEFAULT_RANGE = timedelta(hours=6)
MAX_RANGE = timedelta(days=8)


@router.get("/{resident_id}")
async def get_insights(
    resident_id: int,
    conn: TigerConn,
    kind: str = Query("anomaly", pattern=f"^({'|'.join(KINDS)})$"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
) -> dict:
    """Insights known by `to` (default now) for buckets/moments from `from` (default 6 h earlier)."""
    end = parse_ts(to, "to") or datetime.now(UTC)
    start = parse_ts(from_, "from") or end - DEFAULT_RANGE
    if start >= end:
        raise HTTPException(400, "from must be before to")
    if end - start > MAX_RANGE:
        raise HTTPException(400, f"range is longer than {MAX_RANGE.days} days")
    if kind != "anomaly":
        rows = await tiger.insights_range(conn, resident_id, kind, start, end)
        return {
            "resident_id": resident_id,
            "kind": kind,
            "items": [{"as_of": r["as_of"], **r["payload"]} for r in rows],
        }
    # A bucket's result is stamped with when it became known (bucket end + reveal delay).
    rows = await tiger.insights_range(conn, resident_id, kind, start, end + BUCKET + REVEAL_DELAY)
    items = []
    for r in rows:
        if r["as_of"] > end or datetime.fromisoformat(r["payload"]["bucket"]) < start:
            continue
        items.append({"as_of": r["as_of"], **r["payload"]})
    return {
        "resident_id": resident_id,
        "kind": kind,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "source": "Snowflake SNOWFLAKE.ML.ANOMALY_DETECTION (cached in Tiger ai_insights)",
        "items": items,
    }
