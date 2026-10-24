"""SNOWFLAKE.ML.FORECAST on a resident entering HIGH/CRITICAL (SPEC §9 ③). Built in Phase 9.

`prepare_replay_forecasts` is the hook the replay prepare step calls with every (resident, time) that enters
HIGH/CRITICAL on the replay night. Phase 9 trains the 48 h single-series models there and stores the 2 h
forecasts in Tiger `ai_insights` (kind = forecast, as_of = that time), which the assessment context and the
charts already read. Until then it records the requests and does nothing.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

STATUS = "not built yet (Phase 9)"


async def prepare_replay_forecasts(conn: psycopg.AsyncConnection, requests: list[tuple[int, datetime]]) -> dict:
    """Forecasts for (resident_id, as_of) pairs, stored keyed by as_of. Phase 9 fills this in."""
    return {"status": STATUS, "requested": len(requests), "stored": 0}
