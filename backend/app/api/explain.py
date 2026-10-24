"""POST /api/explain/{id}: ask Snowflake Cortex for a fresh AI assessment now (the card's Refresh, SPEC §12).

Waits at most 8 s. If Cortex is slow or Snowflake is down, it answers with the deterministic template (same
shape, `source: "template"`, plus `fallback_reason`) instead of an error; a slow call keeps running
(`pending: true`) and its result reaches the page via the /ws/live `insight` message.
"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.api.deps import TigerConn, parse_ts
from app.db import tiger
from app.deep.assess import get_assessor, template_assessment
from app.scoring import evaluator as ev

router = APIRouter(prefix="/api/explain", tags=["insights"])


@router.post("/{resident_id}")
async def explain(resident_id: int, conn: TigerConn, as_of: str | None = None) -> dict:
    ts = parse_ts(as_of)
    rows = await tiger.fetch_dicts(conn, "SELECT * FROM residents WHERE resident_id = %s", (resident_id,))
    if not rows:
        raise HTTPException(404, f"no resident {resident_id}")
    assessor = get_assessor()
    at = ts or datetime.now(UTC)
    # The fallback's score card is read while Cortex works, so a fallback still answers within the 8 s budget.
    current, (payload, reason) = await asyncio.gather(
        ev.current_for(conn, resident_id, at, live=ts is None),
        assessor.explain(resident_id, ts, trigger="refresh"),
    )
    if payload is not None:
        return payload | {"pending": False}
    return template_assessment(rows[0], current) | {
        "as_of": at.isoformat(),
        "fallback_reason": reason,
        "pending": assessor.pending(resident_id, ts),
    }
