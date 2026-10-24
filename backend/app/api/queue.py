"""GET /api/queue — the ranked Rounds Queue (SPEC §7d, §12)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from app.api.deps import TigerConn, facility_tz, parse_ts
from app.scoring import evaluator as ev
from app.sim import replay
from app.state import sim_state

router = APIRouter(prefix="/api", tags=["queue"])

LIVE_FRESH = timedelta(seconds=10)


@router.get("/queue")
async def get_queue(conn: TigerConn, as_of: str | None = None) -> dict:
    """Without `as_of`: the live evaluator's latest gated queue (hysteresis, persistence, suppression); frozen
    while the simulator is paused; during a replay, the replay's queue at its cursor. With `as_of` (ISO 8601, no offset = UTC): one stateless evaluation of the
    whole pipeline at that time, judged by the real facility clock (no force-night / display offset)."""
    ts = parse_ts(as_of)
    if ts is None and sim_state.mode == "replay" and (q := replay.current_queue()) is not None:
        return q.to_dict()  # the replay's gated queue at the cursor
    live = ev.evaluator
    latest = live.latest if live is not None and ts is None else None
    if latest is not None and (sim_state.paused or datetime.now(UTC) - latest.as_of < LIVE_FRESH):
        return latest.to_dict()
    ctx = live.ctx if live is not None else None
    queue = await ev.evaluate_at(conn, ts or datetime.now(UTC), facility_tz(), ctx, live=ts is None)
    return queue.to_dict()
