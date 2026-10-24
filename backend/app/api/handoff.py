"""Shift handoff (SPEC §9 ⑦, §11.3): GET the stored one, POST to generate with AI_AGG.

* `GET /api/handoff?shift=night&date=YYYY-MM-DD`: the latest handoff for that shift (this process, else
  Snowflake CORE.HANDOFFS), or `{"handoff": null}`. Without `date`: the most recent shift of that kind that started.
* `POST /api/handoff {shift, date}`: gather the shift from Tiger, AI_AGG it (falls back to the deterministic handoff
  when Snowflake is slow or down), store, return.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import TigerConn, facility_tz
from app.deep import handoff

router = APIRouter(prefix="/api/handoff", tags=["handoff"])


class HandoffIn(BaseModel):
    shift: str = Field("night", pattern="^(day|evening|night)$")
    date: str | None = None  # the shift's start date (facility time)


def _day(shift: str, value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(400, f"date: not YYYY-MM-DD: {value!r}") from exc
    tz = facility_tz()
    now = datetime.now(UTC).astimezone(tz)
    day = now.date()
    start, _ = handoff.shift_window(shift, day, tz)
    return day if start <= datetime.now(UTC) else day - timedelta(days=1)


@router.get("")
async def get_handoff(shift: str = Query("night", pattern="^(day|evening|night)$"), date: str | None = None) -> dict:
    day = _day(shift, date)
    return {"shift": shift, "date": day.isoformat(), "handoff": await handoff.cached(shift, day)}


@router.post("")
async def make_handoff(body: HandoffIn, conn: TigerConn) -> dict:
    day = _day(body.shift, body.date)
    try:
        result = await handoff.generate(conn, body.shift, day, facility_tz())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"shift": body.shift, "date": day.isoformat(), "handoff": result}
