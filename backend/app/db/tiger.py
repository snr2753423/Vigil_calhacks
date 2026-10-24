"""The only module that talks to Tiger Cloud (TimescaleDB). See AGENTS.md rule 4.

Plain async connections, small query helpers, the migration runner, and fast bulk loading:
`encode_copy_binary` turns numpy columns into PostgreSQL binary COPY bytes without a per-row
Python loop, which is what lets the seed push millions of vitals rows in seconds.
"""

import asyncio
import re
import struct
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from app.config import BACKEND_DIR, get_settings

CONNECT_TIMEOUT_S = 10
MIGRATIONS_DIR = BACKEND_DIR / "sql" / "tiger"


class TigerNotConfigured(RuntimeError):
    pass


@asynccontextmanager
async def connect(url: str | None = None, application_name: str = "vigil") -> AsyncIterator[psycopg.AsyncConnection]:
    url = url or get_settings().tiger_database_url
    if not url:
        raise TigerNotConfigured("TIGER_DATABASE_URL is not set in .env")
    conn = await psycopg.AsyncConnection.connect(
        url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S, application_name=application_name
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


# --------------------------------------------------------------------------- migrations

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


def split_sql(text: str) -> list[str]:
    """Split a SQL script into statements on top-level `;`.

    Respects '…' strings, "…" identifiers, $tag$…$tag$ bodies and -- / /* */ comments. Each statement
    runs on its own in autocommit, because TimescaleDB refuses some calls (CALL add_columnstore_policy,
    continuous-aggregate refreshes) inside the implicit transaction of a multi-statement query.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
            buf.append("\n")
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            buf.append(" ")
            continue
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:  # doubled quote = escaped
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i : j + 1])
            i = j + 1
            continue
        if ch == "$" and (tag := _DOLLAR_TAG.match(text, i)):
            end = text.find(tag.group(), tag.end())
            end = n if end == -1 else end + len(tag.group())
            buf.append(text[i:end])
            i = end
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


@dataclass
class MigrationResult:
    file: str
    statements: int
    ms: float


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.sql"))


async def apply_migrations(conn: psycopg.AsyncConnection, files: Iterable[Path] | None = None) -> list[MigrationResult]:
    """Run every migration file in name order. Files are idempotent, so re-running is safe."""
    results = []
    for path in files if files is not None else migration_files():
        start = time.perf_counter()
        statements = split_sql(path.read_text())
        for stmt in statements:
            try:
                await execute(conn, stmt)
            except psycopg.Error as exc:
                first_line = stmt.splitlines()[0][:100]
                raise RuntimeError(f"{path.name}: statement failed ({first_line} …): {exc}") from exc
        results.append(MigrationResult(path.name, len(statements), (time.perf_counter() - start) * 1000))
    return results


# --------------------------------------------------------------------------- binary COPY

PG_EPOCH_US = 946_684_800_000_000  # 2000-01-01T00:00:00Z in unix microseconds
COPY_HEADER = b"PGCOPY\n\xff\r\n\x00" + struct.pack(">ii", 0, 0)
COPY_TRAILER = struct.pack(">h", -1)

# pg type → (numpy big-endian dtype, byte width). Inputs per type:
#   timestamptz: int64 unix microseconds · int2/int4: ints (never NULL)
#   float4: floats, NaN = NULL · bool: int8 with 1/0, -1 = NULL
_COPY_TYPES = {
    "timestamptz": (">i8", 8),
    "int4": (">i4", 4),
    "int2": (">i2", 2),
    "float4": (">f4", 4),
    "bool": ("u1", 1),
}


def _null_mask(values: np.ndarray, pg_type: str) -> np.ndarray | None:
    if pg_type == "float4":
        return np.isnan(values)
    if pg_type == "bool":
        return values < 0
    return None


def encode_copy_binary(columns: Sequence[tuple[str, str]], data: Mapping[str, np.ndarray]) -> bytes:
    """Encode numpy columns as a PostgreSQL `COPY … FROM STDIN (FORMAT BINARY)` payload.

    Rows are grouped by which columns are NULL; every group has a fixed byte layout, so it is
    written as one packed numpy structured array. COPY does not care about row order.
    """
    if len(columns) > 62:
        raise ValueError("too many columns for the null bitmask")
    n = len(data[columns[0][0]])
    null_bits = np.zeros(n, dtype=np.int64)
    for i, (name, pg_type) in enumerate(columns):
        if len(data[name]) != n:
            raise ValueError(f"column {name} has {len(data[name])} values, expected {n}")
        mask = _null_mask(np.asarray(data[name]), pg_type)
        if mask is not None:
            null_bits |= mask.astype(np.int64) << i

    parts = [COPY_HEADER]
    for pattern in np.unique(null_bits):
        idx = np.flatnonzero(null_bits == pattern)
        fields: list[tuple[str, str]] = [("nf", ">i2")]
        for i, (_, pg_type) in enumerate(columns):
            fields.append((f"l{i}", ">i4"))
            if not (pattern >> i) & 1:
                fields.append((f"v{i}", _COPY_TYPES[pg_type][0]))
        rec = np.empty(len(idx), dtype=np.dtype(fields))
        rec["nf"] = len(columns)
        for i, (name, pg_type) in enumerate(columns):
            if (pattern >> i) & 1:
                rec[f"l{i}"] = -1
                continue
            rec[f"l{i}"] = _COPY_TYPES[pg_type][1]
            values = np.asarray(data[name])[idx]
            if pg_type == "timestamptz":
                values = values.astype(np.int64) - PG_EPOCH_US
            rec[f"v{i}"] = values
        parts.append(rec.tobytes())
    parts.append(COPY_TRAILER)
    return b"".join(parts)


async def copy_binary(
    conn: psycopg.AsyncConnection, table: str, columns: Sequence[tuple[str, str]], payload: bytes
) -> None:
    cols = ", ".join(name for name, _ in columns)
    async with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN (FORMAT BINARY)") as copy:
        await copy.write(payload)


async def copy_rows(conn: psycopg.AsyncConnection, table: str, columns: Sequence[str], rows: Iterable[Sequence]) -> int:
    """Text-format COPY for small, mixed-type batches (events with jsonb payloads)."""
    count = 0
    async with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            await copy.write_row(row)
            count += 1
    return count


# --------------------------------------------------------------------------- vitals / events layout

VITALS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ts", "timestamptz"),
    ("resident_id", "int4"),
    ("hr", "float4"),
    ("hrv_rmssd", "float4"),
    ("skin_temp", "float4"),
    ("spo2", "float4"),
    ("motion", "float4"),
    ("impact", "float4"),
    ("worn", "bool"),
    ("rr", "float4"),
    ("in_bed", "bool"),
    ("bed_motion", "float4"),
    ("source", "int2"),
)
EVENT_COLUMNS = ("ts", "resident_id", "type", "staff_id", "payload")
CAGGS = ("vitals_1m", "vitals_15m", "vitals_1h")  # refresh in this order (hierarchical)


def concat_columns(parts: Sequence[Mapping[str, np.ndarray]]) -> dict[str, np.ndarray]:
    return {name: np.concatenate([p[name] for p in parts]) for name, _ in VITALS_COLUMNS}


async def write_vitals(conn: psycopg.AsyncConnection, cols: Mapping[str, np.ndarray]) -> int:
    """Bulk-insert simulated/device rows given as numpy columns in the VITALS_COLUMNS layout."""
    n = len(cols["ts"])
    if n:
        await copy_binary(conn, "vitals", VITALS_COLUMNS, encode_copy_binary(VITALS_COLUMNS, cols))
    return n


async def write_events(conn: psycopg.AsyncConnection, events: Iterable[Sequence]) -> int:
    """Insert (ts_epoch_s, resident_id, type, staff_id, payload) tuples into `events`."""
    rows = (
        (datetime.fromtimestamp(ts, UTC), rid, type_, staff_id, Jsonb(payload))
        for ts, rid, type_, staff_id, payload in events
    )
    return await copy_rows(conn, "events", EVENT_COLUMNS, rows)


async def refresh_caggs(
    conn: psycopg.AsyncConnection, start: datetime | None, end: datetime | None, lock_wait_s: float = 120
) -> None:
    """Refresh vitals_1m → vitals_15m → vitals_1h over [start, end) (None = unbounded).

    A refresh-policy run touching the same window makes TimescaleDB refuse with LockNotAvailable;
    wait and retry rather than fail.
    """
    for cagg in CAGGS:
        deadline = time.monotonic() + lock_wait_s
        while True:
            try:
                await execute(
                    conn, "CALL refresh_continuous_aggregate(%s, %s::timestamptz, %s::timestamptz)", (cagg, start, end)
                )
                break
            except psycopg.errors.LockNotAvailable:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(2)


# The seed holds this advisory lock exclusively while it rewrites history; the live loop holds it shared
# around each write, so the two never interleave (the live loop pauses, then resumes from the new end).
SEED_LOCK_KEY = 0x56_49_47_49_4C  # "VIGIL"


async def try_lock_shared_for_write(conn: psycopg.AsyncConnection) -> bool:
    (ok,) = await fetchone(conn, "SELECT pg_try_advisory_lock_shared(%s)", (SEED_LOCK_KEY,))
    return bool(ok)


async def unlock_shared_for_write(conn: psycopg.AsyncConnection) -> None:
    await execute(conn, "SELECT pg_advisory_unlock_shared(%s)", (SEED_LOCK_KEY,))


async def lock_for_seed(conn: psycopg.AsyncConnection) -> None:
    """Block until no live-loop write is in flight, then keep live writers out until unlock_for_seed()."""
    await execute(conn, "SELECT pg_advisory_lock(%s)", (SEED_LOCK_KEY,))


async def unlock_for_seed(conn: psycopg.AsyncConnection) -> None:
    await execute(conn, "SELECT pg_advisory_unlock(%s)", (SEED_LOCK_KEY,))


# Only one live loop may stream simulated rows at a time (two backends would double every row).
LIVE_WRITER_LOCK_KEY = SEED_LOCK_KEY + 1


async def try_lock_live_writer(conn: psycopg.AsyncConnection) -> bool:
    """Session-level lease, held for as long as this connection stays open."""
    (ok,) = await fetchone(conn, "SELECT pg_try_advisory_lock(%s)", (LIVE_WRITER_LOCK_KEY,))
    return bool(ok)


# --------------------------------------------------------------------------- sim_meta


async def get_meta(conn: psycopg.AsyncConnection, key: str) -> dict | None:
    row = await fetchone(conn, "SELECT value FROM sim_meta WHERE key = %s", (key,))
    return row[0] if row else None


async def set_meta(conn: psycopg.AsyncConnection, key: str, value: dict, merge: bool = False) -> None:
    """Upsert a sim_meta key; merge=True keeps keys of the stored object that `value` does not set."""
    update = "sim_meta.value || excluded.value" if merge else "excluded.value"
    await execute(
        conn,
        f"""INSERT INTO sim_meta (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = {update}, updated_at = now()""",
        (key, Jsonb(value)),
    )
