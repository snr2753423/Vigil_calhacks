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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import BACKEND_DIR, get_settings
from app.scoring.rules import FALL_IMPACT_G

CONNECT_TIMEOUT_S = 10
# TCP keepalives: something on the network path to Tiger Cloud drops connections that stay quiet for minutes
# (the server itself has no idle timeout). A connection held across a long Snowflake call (training,
# detection) then fails on its next query. Probing every 30 s keeps it open.
KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5}
MIGRATIONS_DIR = BACKEND_DIR / "sql" / "tiger"
ANOMALY_LOOKBACK_MIN = 45  # Snowflake anomaly results older than this are stale for scoring (Tiger z instead)


class TigerNotConfigured(RuntimeError):
    pass


def cancelling() -> bool:
    """True while the current task is being cancelled.

    A cancel that lands mid-COPY can surface as psycopg's QueryCanceled (the COPY is failed with the
    CancelledError as reason), so a reconnect-on-error loop must check this before swallowing an error,
    or the task survives shutdown and blocks uvicorn's reload.
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


@asynccontextmanager
async def connect(url: str | None = None, application_name: str = "vigil") -> AsyncIterator[psycopg.AsyncConnection]:
    url = url or get_settings().tiger_database_url
    if not url:
        raise TigerNotConfigured("TIGER_DATABASE_URL is not set in .env")
    conn = await psycopg.AsyncConnection.connect(
        url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S, application_name=application_name, **KEEPALIVE
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


# One statement (a writable CTE) = one round trip, and the rows and events commit together.
DEVICE_BATCH_INSERT = """
    WITH v AS (
        INSERT INTO vitals (ts, resident_id, hr, worn, in_bed, bed_motion, source)
        SELECT * FROM unnest(%s::timestamptz[], %s::int[], %s::real[], %s::bool[], %s::bool[], %s::real[],
                             %s::smallint[])
    )
    INSERT INTO events (ts, resident_id, type, staff_id, payload)
    SELECT * FROM unnest(%s::timestamptz[], %s::int[], %s::text[], %s::int[], %s::jsonb[])
"""


async def write_device_batch(
    conn: psycopg.AsyncConnection, rows: Sequence[Sequence], events: Sequence[Sequence]
) -> None:
    """Live device rows `(ts_epoch_s, rid, hr, worn, in_bed, bed_motion, source)` and events
    `(ts_epoch_s, rid, type, staff_id, payload)` in one statement: a bed exit's `in_bed = false` row and its event
    land atomically in a single round trip (~0.3 s to Tiger Cloud, vs ~1.4 s for two COPYs and a commit)."""

    def col(seq: Sequence[Sequence], i: int) -> list:
        return [r[i] for r in seq]

    def ts(seq: Sequence[Sequence]) -> list[datetime]:
        return [datetime.fromtimestamp(r[0], UTC) for r in seq]

    params = (
        ts(rows), *(col(rows, i) for i in range(1, 7)),
        ts(events), col(events, 1), col(events, 2), col(events, 3), [Jsonb(e[4]) for e in events],
    )  # fmt: skip
    await execute(conn, DEVICE_BATCH_INSERT, params)


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


# --------------------------------------------------------------------------- API connection pool

_pool: AsyncConnectionPool | None = None
POOL_TIMEOUT_S = 10


async def open_pool(min_size: int = 1, max_size: int = 6) -> AsyncConnectionPool:
    """Shared pool for API requests (a fresh TLS connection to Tiger Cloud costs ~0.5 s)."""
    global _pool
    if _pool is None:
        url = get_settings().tiger_database_url
        if not url:
            raise TigerNotConfigured("TIGER_DATABASE_URL is not set in .env")
        pool = AsyncConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs={
                "autocommit": True,
                "connect_timeout": CONNECT_TIMEOUT_S,
                "application_name": "vigil-api",
                **KEEPALIVE,
            },
            open=False,
        )
        await pool.open(wait=False)
        _pool = pool
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        pool, _pool = _pool, None
        await pool.close()


@asynccontextmanager
async def pooled() -> AsyncIterator[psycopg.AsyncConnection]:
    pool = await open_pool()
    async with pool.connection(timeout=POOL_TIMEOUT_S) as conn:
        yield conn


async def fetch_dicts(conn: psycopg.AsyncConnection, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None):
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


# --------------------------------------------------------------------------- fast-tier scoring inputs
# One batched read for all residents as of `as_of` (SPEC §7a). The queries go out in a single pipeline,
# so the evaluator pays one network round trip per cycle. Every bound is relative to %(as_of)s.


SCORING_QUERIES: dict[str, str] = {
    # Raw vitals, trailing 15 min: liveness, latest state, rule windows, NEWS2 averages.
    "raw": """
        WITH w AS (
            SELECT ts, resident_id, hr, rr, spo2, worn, in_bed
            FROM vitals
            WHERE ts > %(as_of)s - INTERVAL '15 minutes' AND ts <= %(as_of)s
        )
        SELECT resident_id,
               max(ts)  AS last_ts,
               count(*) AS n,
               live_at(heartbeat_agg(ts, %(as_of)s - INTERVAL '15 minutes', INTERVAL '15 minutes 1 second',
                                     INTERVAL '5 minutes'), %(as_of)s) AS heartbeat_live,
               (array_agg(worn ORDER BY ts DESC) FILTER (WHERE worn IS NOT NULL))[1]     AS worn,
               (array_agg(in_bed ORDER BY ts DESC) FILTER (WHERE in_bed IS NOT NULL))[1] AS in_bed,
               max(ts) FILTER (WHERE worn)              AS last_worn_ts,
               max(ts) FILTER (WHERE hr IS NOT NULL)    AS last_hr_ts,
               avg(hr) FILTER (WHERE ts > %(as_of)s - INTERVAL '5 minutes') AS hr_5m,
               avg(rr) FILTER (WHERE ts > %(as_of)s - INTERVAL '5 minutes') AS rr_5m,
               (array_agg(spo2 ORDER BY ts DESC) FILTER (WHERE spo2 IS NOT NULL))[1:2] AS spo2_recent,
               count(*) FILTER (WHERE ts > %(as_of)s - INTERVAL '65 seconds' AND worn AND hr IS NOT NULL) AS hr60_n,
               min(hr)  FILTER (WHERE ts > %(as_of)s - INTERVAL '65 seconds' AND worn) AS hr60_lo,
               max(hr)  FILTER (WHERE ts > %(as_of)s - INTERVAL '65 seconds' AND worn) AS hr60_hi,
               extract(epoch FROM max(ts) FILTER (WHERE ts > %(as_of)s - INTERVAL '65 seconds' AND worn AND hr IS NOT NULL)
                                - min(ts) FILTER (WHERE ts > %(as_of)s - INTERVAL '65 seconds' AND worn AND hr IS NOT NULL)
               )::float AS hr60_span,
               count(*) FILTER (WHERE ts > %(as_of)s - INTERVAL '125 seconds' AND in_bed AND rr IS NOT NULL) AS rr120_n,
               min(rr)  FILTER (WHERE ts > %(as_of)s - INTERVAL '125 seconds' AND in_bed) AS rr120_lo,
               max(rr)  FILTER (WHERE ts > %(as_of)s - INTERVAL '125 seconds' AND in_bed) AS rr120_hi,
               extract(epoch FROM max(ts) FILTER (WHERE ts > %(as_of)s - INTERVAL '125 seconds' AND in_bed AND rr IS NOT NULL)
                                - min(ts) FILTER (WHERE ts > %(as_of)s - INTERVAL '125 seconds' AND in_bed AND rr IS NOT NULL)
               )::float AS rr120_span
        FROM w
        GROUP BY resident_id
    """,
    # Newest impact spike within 2 h (partial index vitals_impact_idx), what followed it. The threshold is
    # interpolated as a literal so the planner can use the partial index.
    "impact": f"""
        WITH imp AS (
            SELECT DISTINCT ON (resident_id) resident_id, ts, impact
            FROM vitals
            WHERE impact >= {float(FALL_IMPACT_G)} AND ts > %(as_of)s - INTERVAL '2 hours' AND ts <= %(as_of)s
            ORDER BY resident_id, ts DESC
        )
        SELECT imp.resident_id, imp.ts, imp.impact, a.motion_after, a.rows_after, b.back_in_bed
        FROM imp
        CROSS JOIN LATERAL (
            SELECT max(v.motion) AS motion_after, count(*) AS rows_after
            FROM vitals v
            WHERE v.resident_id = imp.resident_id AND v.ts > imp.ts
              AND v.ts <= least(imp.ts + INTERVAL '30 seconds', %(as_of)s)
        ) a
        CROSS JOIN LATERAL (
            SELECT coalesce(bool_or(v.in_bed), false) AS back_in_bed
            FROM vitals v
            WHERE v.resident_id = imp.resident_id AND v.ts > imp.ts AND v.ts <= %(as_of)s
        ) b
    """,
    # Closed 1-minute buckets, trailing 6 h: two 15-min windows vs baseline, stillness, HR sparkline.
    "minute": """
        WITH m AS (
            SELECT bucket, resident_id, hr_avg, rr_avg, spo2_min, skin_temp_avg, motion_sum, in_bed_frac, worn_frac,
                   -- first minute after a data gap (restart, network): stillness can't be inferred across it
                   bucket - lag(bucket) OVER (PARTITION BY resident_id ORDER BY bucket) > INTERVAL '1 minute'
                       AS after_gap
            FROM vitals_1m
            WHERE bucket > %(as_of)s - INTERVAL '6 hours' AND bucket <= %(as_of)s - INTERVAL '1 minute'
        ), agg AS (
            SELECT resident_id,
                   avg(hr_avg)        FILTER (WHERE bucket > %(as_of)s - INTERVAL '16 minutes') AS hr_w0,
                   avg(rr_avg)        FILTER (WHERE bucket > %(as_of)s - INTERVAL '16 minutes') AS rr_w0,
                   avg(spo2_min)      FILTER (WHERE bucket > %(as_of)s - INTERVAL '16 minutes') AS spo2_w0,
                   avg(skin_temp_avg) FILTER (WHERE bucket > %(as_of)s - INTERVAL '16 minutes') AS skin_temp_w0,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY motion_sum)
                                      FILTER (WHERE bucket > %(as_of)s - INTERVAL '16 minutes') AS motion_w0,
                   avg(in_bed_frac)   FILTER (WHERE bucket > %(as_of)s - INTERVAL '16 minutes') AS in_bed_w0,
                   avg(hr_avg)        FILTER (WHERE bucket > %(as_of)s - INTERVAL '31 minutes'
                                                AND bucket <= %(as_of)s - INTERVAL '16 minutes') AS hr_w1,
                   avg(rr_avg)        FILTER (WHERE bucket > %(as_of)s - INTERVAL '31 minutes'
                                                AND bucket <= %(as_of)s - INTERVAL '16 minutes') AS rr_w1,
                   avg(spo2_min)      FILTER (WHERE bucket > %(as_of)s - INTERVAL '31 minutes'
                                                AND bucket <= %(as_of)s - INTERVAL '16 minutes') AS spo2_w1,
                   avg(skin_temp_avg) FILTER (WHERE bucket > %(as_of)s - INTERVAL '31 minutes'
                                                AND bucket <= %(as_of)s - INTERVAL '16 minutes') AS skin_temp_w1,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY motion_sum)
                                      FILTER (WHERE bucket > %(as_of)s - INTERVAL '31 minutes'
                                                AND bucket <= %(as_of)s - INTERVAL '16 minutes') AS motion_w1,
                   avg(in_bed_frac)   FILTER (WHERE bucket > %(as_of)s - INTERVAL '31 minutes'
                                                AND bucket <= %(as_of)s - INTERVAL '16 minutes') AS in_bed_w1,
                   -- minutes of observed stillness: from the last minute with wrist motion (band-off minutes
                   -- and data gaps reset the clock) to the newest minute with data (a trailing gap adds nothing)
                   extract(epoch FROM max(bucket) + INTERVAL '1 minute' - greatest(
                       max(bucket) FILTER (WHERE motion_sum >= %(still_motion)s OR motion_sum IS NULL
                                                 OR worn_frac < 0.5) + INTERVAL '1 minute',
                       max(bucket) FILTER (WHERE after_gap),
                       min(bucket)))::float / 60 AS still_minutes
            FROM m
            GROUP BY resident_id
        ), spark AS (
            SELECT resident_id, array_agg(hr ORDER BY b) AS hr_spark
            FROM (SELECT resident_id, time_bucket(INTERVAL '5 minutes', bucket) AS b, avg(hr_avg)::real AS hr
                  FROM m WHERE bucket > %(as_of)s - INTERVAL '2 hours'
                  GROUP BY 1, 2) s
            GROUP BY resident_id
        )
        SELECT agg.*, spark.hr_spark FROM agg LEFT JOIN spark USING (resident_id)
    """,
    # Staff and bed events, trailing 24 h.
    "events": """
        SELECT resident_id,
               max(ts) FILTER (WHERE type = 'check_logged') AS last_check,
               max(ts) FILTER (WHERE type = 'bed_exit')     AS last_exit,
               max(ts) FILTER (WHERE type = 'bed_return')   AS last_return,
               count(*) FILTER (WHERE type = 'bed_exit' AND ts >= %(night_start)s::timestamptz) AS exits_tonight,
               (array_agg(payload ORDER BY ts DESC)
                   FILTER (WHERE type = 'spot_check' AND ts > %(as_of)s - INTERVAL '12 hours'))[1] AS spot,
               max(ts) FILTER (WHERE type = 'spot_check' AND ts > %(as_of)s - INTERVAL '12 hours') AS spot_ts,
               (array_agg(payload ORDER BY ts DESC)
                   FILTER (WHERE type = 'note_added' AND ts > %(as_of)s - INTERVAL '12 hours'))[1] AS note,
               max(ts) FILTER (WHERE type = 'note_added' AND ts > %(as_of)s - INTERVAL '12 hours') AS note_ts
        FROM events
        WHERE ts > %(as_of)s - INTERVAL '24 hours' AND ts <= %(as_of)s
        GROUP BY resident_id
    """,
    # Snowflake note extractions mirrored to Tiger (Phase 6); empty until then.
    "note_extract": """
        SELECT DISTINCT ON (resident_id) resident_id, as_of, payload
        FROM ai_insights
        WHERE kind = 'note_extract' AND as_of > %(as_of)s - INTERVAL '12 hours' AND as_of <= %(as_of)s
        ORDER BY resident_id, as_of DESC
    """,
    # Snowflake ANOMALY_DETECTION results (as_of = bucket end): the newest 2 buckets known by as_of,
    # if fresh enough. Older than the lookback → the fast tier falls back to Tiger z-scores.
    "anomaly": f"""
        SELECT resident_id, as_of, payload
        FROM (SELECT resident_id, as_of, payload,
                     row_number() OVER (PARTITION BY resident_id ORDER BY as_of DESC) AS rn
              FROM ai_insights
              WHERE kind = 'anomaly' AND as_of <= %(as_of)s
                AND as_of > %(as_of)s - INTERVAL '{ANOMALY_LOOKBACK_MIN} minutes') s
        WHERE rn <= 2
        ORDER BY resident_id, as_of DESC
    """,
}


async def load_scoring_rows(
    conn: psycopg.AsyncConnection, as_of: datetime, night_start: datetime | None, still_motion: float
) -> dict[str, list[dict]]:
    params = {"as_of": as_of, "night_start": night_start, "still_motion": still_motion}
    cursors = {}
    async with conn.pipeline():
        for name, sql in SCORING_QUERIES.items():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            cursors[name] = cur
    out = {}
    for name, cur in cursors.items():
        out[name] = await cur.fetchall()
        await cur.close()
    return out


async def load_profiles(conn: psycopg.AsyncConnection) -> list[dict]:
    return await fetch_dicts(
        conn,
        """SELECT resident_id, name, sex, room, unit, mobility, morse, cognition, code_status, flags, is_live
           FROM residents ORDER BY resident_id""",
    )


async def load_baseline_rows(conn: psycopg.AsyncConnection) -> list[tuple]:
    return await fetchall(
        conn, "SELECT resident_id, tod_block, metric, p05, p50, p95, mean, stddev FROM resident_baselines"
    )


SNAPSHOT_COLUMNS = ("ts", "resident_id", "score", "tier", "components", "factors")


async def write_snapshots(conn: psycopg.AsyncConnection, rows: Iterable[Sequence]) -> int:
    """(ts, resident_id, score, tier, components dict, factors list) → priority_snapshots."""
    return await copy_rows(
        conn,
        "priority_snapshots",
        SNAPSHOT_COLUMNS,
        ((ts, rid, s, t, Jsonb(c), Jsonb(f)) for ts, rid, s, t, c, f in rows),
    )


SNAPSHOT_TABLES = ("priority_snapshots", "replay_snapshots")  # live scores · the replay prepare pass's


async def upsert_replay_snapshots(conn: psycopg.AsyncConnection, rows: Sequence[Sequence]) -> int:
    """(ts, resident_id, score, tier, factors list) → replay_snapshots, replacing a re-prepared step in place."""
    if not rows:
        return 0
    ts, rid, score, tier, factors = (list(c) for c in zip(*rows, strict=True))
    await execute(
        conn,
        """INSERT INTO replay_snapshots (ts, resident_id, score, tier, factors)
           SELECT * FROM unnest(%s::timestamptz[], %s::int[], %s::real[], %s::text[], %s::jsonb[])
           ON CONFLICT (resident_id, ts) DO UPDATE
               SET score = excluded.score, tier = excluded.tier, factors = excluded.factors""",
        (ts, rid, score, tier, [Jsonb(f) for f in factors]),
    )
    return len(rows)


async def latest_snapshots(
    conn: psycopg.AsyncConnection, as_of: datetime, lookback_s: float = 600, table: str = "priority_snapshots"
) -> list[dict]:
    assert table in SNAPSHOT_TABLES
    return await fetch_dicts(
        conn,
        f"""SELECT DISTINCT ON (resident_id) resident_id, ts, score, tier, factors
           FROM {table}
           WHERE ts > %(as_of)s - make_interval(secs => %(lookback)s) AND ts <= %(as_of)s
           ORDER BY resident_id, ts DESC""",
        {"as_of": as_of, "lookback": lookback_s},
    )


async def snapshot_scores_before(
    conn: psycopg.AsyncConnection, points: Mapping[int, datetime], table: str = "priority_snapshots"
) -> dict[int, float]:
    """Score recorded just before each (resident, time), within 5 min — e.g. the score when a check was logged."""
    if not points:
        return {}
    assert table in SNAPSHOT_TABLES
    rows = await fetchall(
        conn,
        f"""SELECT DISTINCT ON (c.rid) c.rid, s.score
           FROM unnest(%s::int[], %s::timestamptz[]) AS c (rid, ts)
           JOIN {table} s ON s.resident_id = c.rid AND s.ts <= c.ts AND s.ts > c.ts - INTERVAL '5 minutes'
           ORDER BY c.rid, s.ts DESC""",
        (list(points), list(points.values())),
    )
    return {rid: float(score) for rid, score in rows}


# --------------------------------------------------------------------------- charts

# metric → (aggregate expression over the cagg, carry forward across missing buckets)
SERIES_METRICS: dict[str, tuple[str, bool]] = {
    "hr": ("avg(hr_avg)", True),
    "hr_min": ("min(hr_min)", True),
    "hr_max": ("max(hr_max)", True),
    "rr": ("avg(rr_avg)", True),
    "spo2": ("min(spo2_min)", True),
    "skin_temp": ("avg(skin_temp_avg)", True),
    "hrv": ("avg(hrv_avg)", True),
    "motion": ("sum(motion_sum)", False),
    "in_bed": ("avg(in_bed_frac)", False),
    "bed_motion": ("avg(bed_motion_avg)", False),
    "worn": ("avg(worn_frac)", False),
}
SERIES_RES = {"1m": ("vitals_1m", "1 minute"), "15m": ("vitals_15m", "15 minutes"), "1h": ("vitals_1h", "1 hour")}


async def vitals_series(
    conn: psycopg.AsyncConnection, resident_id: int, metrics: Sequence[str], start: datetime, end: datetime, res: str
) -> list[dict]:
    """Gap-filled chart series from a continuous aggregate (time_bucket_gapfill + locf)."""
    table, step = SERIES_RES[res]
    cols = []
    for m in metrics:
        expr, carry = SERIES_METRICS[m]
        cols.append(f"locf({expr}::real) AS {m}" if carry else f"{expr}::real AS {m}")  # locf must be top level
    sql = f"""
        SELECT time_bucket_gapfill(INTERVAL '{step}', bucket, %(start)s, %(end)s) AS ts, {", ".join(cols)}
        FROM {table}
        WHERE resident_id = %(rid)s AND bucket >= %(start)s AND bucket < %(end)s
        GROUP BY 1 ORDER BY 1
    """  # table/step/expressions come from the constant maps above
    return await fetch_dicts(conn, sql, {"rid": resident_id, "start": start, "end": end})


# --------------------------------------------------------------------------- Snowflake sync (SPEC §9 ①)

# Only one process syncs to Snowflake at a time (a seed's bulk load, or one backend's sync loop).
SYNC_LOCK_KEY = SEED_LOCK_KEY + 2


async def try_lock_sync(conn: psycopg.AsyncConnection) -> bool:
    (ok,) = await fetchone(conn, "SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,))
    return bool(ok)


async def lock_sync(conn: psycopg.AsyncConnection) -> None:
    await execute(conn, "SELECT pg_advisory_lock(%s)", (SYNC_LOCK_KEY,))


async def unlock_sync(conn: psycopg.AsyncConnection) -> None:
    await execute(conn, "SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))


async def sync_frontier(conn: psycopg.AsyncConnection, as_of: datetime) -> datetime | None:
    """Newest simulated row at or before as_of: everything older is complete (the live loop and its
    catch-up write all residents tick by tick), so buckets that end before it can be synced."""
    row = await fetchone(
        conn,
        "SELECT max(ts) FROM vitals WHERE source = 0 AND ts > %(as_of)s - INTERVAL '3 days' AND ts <= %(as_of)s",
        {"as_of": as_of},
    )
    return row[0] if row else None


async def get_sync_state(conn: psycopg.AsyncConnection) -> dict[str, dict]:
    rows = await fetch_dicts(conn, "SELECT name, watermark, rows_synced, updated_at FROM sync_state")
    return {r["name"]: r for r in rows}


async def set_sync_state(conn: psycopg.AsyncConnection, name: str, watermark: datetime | None, add_rows: int) -> None:
    await execute(
        conn,
        """INSERT INTO sync_state (name, watermark, rows_synced, updated_at) VALUES (%s, %s, %s, now())
           ON CONFLICT (name) DO UPDATE SET watermark = coalesce(excluded.watermark, sync_state.watermark),
                                            rows_synced = sync_state.rows_synced + excluded.rows_synced,
                                            updated_at = now()""",
        (name, watermark, add_rows),
    )


async def reset_sync_state(conn: psycopg.AsyncConnection) -> None:
    await execute(conn, "DELETE FROM sync_state")


async def vitals_15m_closed(conn: psycopg.AsyncConnection, after: datetime | None, cutoff: datetime) -> list[dict]:
    """vitals_15m buckets that start after `after` (None = all) and end at or before `cutoff`."""
    return await fetch_dicts(
        conn,
        """SELECT resident_id, bucket, hr_avg, hr_min, hr_max, approx_percentile(0.5, hr_pct) AS hr_p50, hrv_avg,
                  rr_avg, rr_min, rr_max, spo2_min, skin_temp_avg, motion_sum, impact_max, in_bed_frac,
                  bed_motion_avg, worn_frac, n
           FROM vitals_15m
           WHERE (%(after)s::timestamptz IS NULL OR bucket > %(after)s) AND bucket <= %(cutoff)s - INTERVAL '15 minutes'
           ORDER BY bucket, resident_id""",
        {"after": after, "cutoff": cutoff},
    )


async def events_between(conn: psycopg.AsyncConnection, after: datetime | None, cutoff: datetime) -> list[dict]:
    return await fetch_dicts(
        conn,
        """SELECT ts, resident_id, type, staff_id, payload FROM events
           WHERE (%(after)s::timestamptz IS NULL OR ts > %(after)s) AND ts <= %(cutoff)s
           ORDER BY ts""",
        {"after": after, "cutoff": cutoff},
    )


async def reference_rows(conn: psycopg.AsyncConnection) -> tuple[list[dict], list[dict]]:
    residents = await fetch_dicts(conn, "SELECT * FROM residents ORDER BY resident_id")
    staff = await fetch_dicts(conn, "SELECT * FROM staff ORDER BY staff_id")
    return residents, staff


# --------------------------------------------------------------------------- ai_insights (Snowflake results cache)


async def upsert_insights(conn: psycopg.AsyncConnection, kind: str, rows: Iterable[tuple[int, datetime, dict]]) -> int:
    """(resident_id, as_of, payload) → ai_insights, replacing any row with the same key."""
    data = [(rid, kind, as_of, Jsonb(payload)) for rid, as_of, payload in rows]
    if not data:
        return 0
    async with conn.cursor() as cur:
        await cur.executemany(
            """INSERT INTO ai_insights (resident_id, kind, as_of, payload) VALUES (%s, %s, %s, %s)
               ON CONFLICT (resident_id, kind, as_of) DO UPDATE SET payload = excluded.payload, created_at = now()""",
            data,
        )
    return len(data)


async def delete_anomaly_insights(conn: psycopg.AsyncConnection, start: datetime, end: datetime) -> None:
    """Remove cached anomaly results for buckets in [start, end) (whatever reveal delay they were stamped with)."""
    await execute(
        conn,
        """DELETE FROM ai_insights
           WHERE kind = 'anomaly' AND as_of > %(start)s AND as_of < %(end)s + INTERVAL '2 hours'
             AND (payload->>'bucket')::timestamptz >= %(start)s AND (payload->>'bucket')::timestamptz < %(end)s""",
        {"start": start, "end": end},
    )


async def insights_range(
    conn: psycopg.AsyncConnection, resident_id: int, kind: str, start: datetime, end: datetime
) -> list[dict]:
    return await fetch_dicts(
        conn,
        """SELECT as_of, payload, created_at FROM ai_insights
           WHERE resident_id = %s AND kind = %s AND as_of > %s AND as_of <= %s ORDER BY as_of""",
        (resident_id, kind, start, end),
    )


async def add_snowflake_calls(conn: psycopg.AsyncConnection, deltas: Mapping[str, Any]) -> None:
    """Add per-feature call counts (objects with calls / errors / total_ms) to the cumulative totals."""
    rows = [(name, d.calls, d.errors, d.total_ms) for name, d in deltas.items() if d.calls]
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            """INSERT INTO snowflake_calls (feature, calls, errors, total_ms) VALUES (%s, %s, %s, %s)
               ON CONFLICT (feature) DO UPDATE SET calls = snowflake_calls.calls + excluded.calls,
                   errors = snowflake_calls.errors + excluded.errors,
                   total_ms = snowflake_calls.total_ms + excluded.total_ms, last_at = now()""",
            rows,
        )


async def snowflake_call_totals(conn: psycopg.AsyncConnection) -> list[dict]:
    return await fetch_dicts(
        conn, "SELECT feature, calls, errors, total_ms, first_at, last_at FROM snowflake_calls ORDER BY feature"
    )


async def insight_counts(conn: psycopg.AsyncConnection) -> dict[str, dict]:
    rows = await fetch_dicts(
        conn,
        """SELECT kind, count(*) AS n, count(DISTINCT resident_id) AS residents, min(as_of) AS first,
                  max(as_of) AS last FROM ai_insights GROUP BY kind""",
    )
    return {r["kind"]: r for r in rows}


# --------------------------------------------------------------------------- proof panel (SPEC §13)

STATS_QUERIES: dict[str, str] = {
    # Rows written in the last minute, by source (0 sim, 1 Polar band, 2 iPhone bed sensor).
    "ingest": """SELECT source, count(*) AS n, max(ts) AS newest FROM vitals
                 WHERE ts > now() - INTERVAL '60 seconds' GROUP BY source ORDER BY source""",
    "rows": """SELECT approximate_row_count('vitals') AS vitals, approximate_row_count('events') AS events,
                      approximate_row_count('priority_snapshots') AS priority_snapshots,
                      approximate_row_count('vitals_1m') AS vitals_1m, approximate_row_count('vitals_15m') AS vitals_15m,
                      approximate_row_count('vitals_1h') AS vitals_1h""",
    "columnstore": """SELECT total_chunks, number_compressed_chunks, before_compression_total_bytes AS before_bytes,
                             after_compression_total_bytes AS after_bytes, hypertable_size('vitals') AS size_bytes
                      FROM hypertable_columnstore_stats('vitals')""",
    "chunks": """SELECT hypertable_name, count(*) AS chunks, count(*) FILTER (WHERE is_compressed) AS compressed,
                        min(range_start) AS oldest, max(range_end) AS newest
                 FROM timescaledb_information.chunks GROUP BY 1 ORDER BY 1""",
    "caggs": """SELECT view_name, materialized_only, materialization_hypertable_name
                FROM timescaledb_information.continuous_aggregates ORDER BY view_name""",
    "jobs": """SELECT j.job_id, j.proc_name, j.hypertable_name, j.schedule_interval::text AS schedule,
                      s.last_run_status, s.last_successful_finish, s.next_start, s.total_runs, s.total_failures,
                      extract(epoch FROM s.last_run_duration) * 1000 AS last_run_ms
               FROM timescaledb_information.jobs j LEFT JOIN timescaledb_information.job_stats s USING (job_id)
               WHERE j.proc_name <> 'policy_telemetry' AND j.proc_name <> 'policy_job_stat_history_retention'
               ORDER BY j.job_id""",
    "extensions": "SELECT extname, extversion FROM pg_extension WHERE extname LIKE 'timescale%' ORDER BY 1",
    "server": "SELECT current_setting('server_version') AS postgres",
}


async def tiger_stats(conn: psycopg.AsyncConnection) -> dict[str, list[dict]]:
    """Every proof query in one pipeline (one round trip), straight from the TimescaleDB catalog."""
    cursors = {}
    async with conn.pipeline():
        for name, sql in STATS_QUERIES.items():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql)
            cursors[name] = cur
    out = {}
    for name, cur in cursors.items():
        out[name] = await cur.fetchall()
        await cur.close()
    return out


# "7-day hourly HR for all 120 residents", from raw rows vs the vitals_1h continuous aggregate. Both are wrapped
# in one outer aggregate so each returns a single row: the timing is the database's work, not the transfer.
BENCH_QUERIES: dict[str, dict[str, str]] = {
    "hourly_hr": {
        "raw": """SELECT count(*) AS rows, avg(hr)::real AS mean_hr FROM (
                      SELECT resident_id, time_bucket(INTERVAL '1 hour', ts) AS bucket, avg(hr) AS hr
                      FROM vitals WHERE ts > now() - INTERVAL '7 days' GROUP BY 1, 2) s""",
        "vitals_1h": """SELECT count(*) AS rows, avg(hr)::real AS mean_hr FROM (
                            SELECT resident_id, bucket, hr_avg AS hr
                            FROM vitals_1h WHERE bucket > now() - INTERVAL '7 days') s""",
    },
}
BENCH_TIMEOUT_MS = 60_000


async def run_bench(conn: psycopg.AsyncConnection, name: str) -> dict[str, dict]:
    """Time each variant of a benchmark (wall clock from this backend, one round trip each)."""
    out = {}
    async with conn.transaction():
        await execute(conn, f"SET LOCAL statement_timeout = {BENCH_TIMEOUT_MS}")
        for variant, sql in BENCH_QUERIES[name].items():
            t0 = time.perf_counter()
            row = (await fetch_dicts(conn, sql))[0]
            out[variant] = {"ms": round((time.perf_counter() - t0) * 1000, 1), **row}
    return out


# Fixed-threshold alarms a conventional monitor would raise (SPEC §13): HR > 100 or < 50, RR > 24 or < 10,
# SpO₂ < 90, any bed exit. Abnormal readings less than `merge` apart are one alarm (a monitor re-alarms only after
# the reading has been back in range for a while), so this is a conservative count.
FIXED_ALARMS_SQL = """
    WITH v AS (
        SELECT resident_id, ts, hr, rr, spo2 FROM vitals WHERE ts >= %(start)s AND ts < %(end)s
    ), f AS (
        SELECT resident_id, ts, 'hr_high' AS kind FROM v WHERE hr > 100
        UNION ALL SELECT resident_id, ts, 'hr_low' FROM v WHERE hr < 50
        UNION ALL SELECT resident_id, ts, 'rr_high' FROM v WHERE rr > 24
        UNION ALL SELECT resident_id, ts, 'rr_low' FROM v WHERE rr < 10
        UNION ALL SELECT resident_id, ts, 'spo2_low' FROM v WHERE spo2 < 90
        UNION ALL SELECT resident_id, ts, 'bed_exit' FROM events
                  WHERE type = 'bed_exit' AND ts >= %(start)s AND ts < %(end)s
    ), e AS (
        SELECT resident_id, kind, ts,
               ts - lag(ts) OVER (PARTITION BY resident_id, kind ORDER BY ts) AS gap
        FROM f
    )
    SELECT resident_id, kind, count(*) AS alarms, min(ts) AS first_ts
    FROM e
    WHERE gap IS NULL OR gap > %(merge)s OR kind = 'bed_exit'
    GROUP BY 1, 2 ORDER BY 1, 2
"""


async def fixed_threshold_alarms(
    conn: psycopg.AsyncConnection, start: datetime, end: datetime, merge: timedelta
) -> list[dict]:
    """Per (resident, kind): how many fixed-threshold alarms in [start, end) and when the first one fired."""
    return await fetch_dicts(conn, FIXED_ALARMS_SQL, {"start": start, "end": end, "merge": merge})
