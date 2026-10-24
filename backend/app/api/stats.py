"""GET /api/stats/snowflake: proof that Snowflake does real work (SPEC §13, AGENTS.md rule 3).

Call counts and average latency per feature (this backend process), the sync loop and its watermarks,
rows synced (cumulative, from Tiger sync_state), the anomaly models (`make train` metadata) and the last
detection run. Works with Tiger or Snowflake down: whatever can't be read is null.
"""

import psycopg
from fastapi import APIRouter

from app.config import get_settings
from app.db import snowflake_rest, tiger
from app.db import snowflake_sql as sf
from app.deep import anomaly, assess, sync

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/snowflake")
async def snowflake_stats() -> dict:
    calls = sf.call_stats.snapshot()
    out: dict = {
        "configured": get_settings().snowflake_configured,
        "calls": calls,
        "totals": {
            "calls": sum(int(c["calls"]) for c in calls.values()),
            "errors": sum(int(c["errors"]) for c in calls.values()),
        },
        "sync": sync.stats.to_dict(),
        "detection": anomaly.stats.to_dict(),
        "assessments": assess.get_assessor().stats.to_dict(),
        "rest": snowflake_rest.client().status(),
        "sync_state": None,
        "models": None,
        "insights": None,
    }
    try:
        async with tiger.pooled() as conn:
            out["sync_state"] = await tiger.get_sync_state(conn)
            out["models"] = await anomaly.models_meta(conn)
            out["insights"] = await tiger.insight_counts(conn)
    except (tiger.TigerNotConfigured, psycopg.Error) as exc:
        out["tiger_error"] = " ".join(str(exc).split())[:200]
    return out
