"""The only module that talks to Tiger Cloud (TimescaleDB). See AGENTS.md rule 4.

Phase 1: plain async connections + small query helpers. A connection pool and
migrations runner arrive with the schema in Phase 2.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg

from app.config import get_settings

CONNECT_TIMEOUT_S = 10


class TigerNotConfigured(RuntimeError):
    pass


@asynccontextmanager
async def connect(url: str | None = None) -> AsyncIterator[psycopg.AsyncConnection]:
    url = url or get_settings().tiger_database_url
    if not url:
        raise TigerNotConfigured("TIGER_DATABASE_URL is not set in .env")
    conn = await psycopg.AsyncConnection.connect(
        url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S, application_name="vigil"
    )
    try:
        yield conn
    finally:
        await conn.close()


async def fetchall(conn: psycopg.AsyncConnection, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def fetchone(conn: psycopg.AsyncConnection, sql: str, params: Sequence[Any] | None = None) -> tuple | None:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def execute(conn: psycopg.AsyncConnection, sql: str, params: Sequence[Any] | None = None) -> None:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
