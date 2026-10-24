"""Shared helpers for API routes: Tiger connections, as_of parsing, the facility time zone."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, HTTPException

from app.config import get_settings
from app.db import tiger

FUTURE_SLACK = timedelta(seconds=5)


def facility_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().facility_tz)


async def tiger_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """FastAPI dependency: a pooled Tiger connection, or 503 if Tiger isn't configured/reachable."""
    try:
        async with tiger.pooled() as conn:
            yield conn
    except tiger.TigerNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except psycopg.OperationalError as exc:
        raise HTTPException(503, f"Tiger unavailable: {' '.join(str(exc).split())[:200]}") from exc


TigerConn = Annotated[psycopg.AsyncConnection, Depends(tiger_conn)]


def refuse_in_replay(what: str) -> None:
    """Writes stamped "now" make no sense while the UI shows last night: say so instead of storing them."""
    from app.state import sim_state

    if sim_state.mode == "replay":
        raise HTTPException(409, f"Replay is read-only: switch back to live to {what}.")


def parse_ts(value: str | None, name: str = "as_of") -> datetime | None:
    """ISO 8601 → aware UTC datetime. A value without an offset is taken as UTC. Future times are refused."""
    if value is None or value == "":
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "+", 1) if "T" in value else value)
    except ValueError as exc:
        raise HTTPException(400, f"{name}: not an ISO 8601 timestamp: {value!r}") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    ts = ts.astimezone(UTC)
    if ts > datetime.now(UTC) + FUTURE_SLACK:
        raise HTTPException(400, f"{name} is in the future")
    return ts
