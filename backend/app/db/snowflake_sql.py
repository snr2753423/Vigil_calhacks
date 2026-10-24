"""The only module that runs SQL on Snowflake (Python connector, key-pair auth). See AGENTS.md rule 3.

Every call is counted with its latency under a feature name (`sync.vitals_15m`, `anomaly.detect.hr`, …);
`/api/stats/snowflake` reads `call_stats`. `snowflake_rest.py` records its calls here too.
"""

import contextlib
import io
import threading
import time
import weakref
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from snowflake.connector.util_text import split_statements

from app.config import BACKEND_DIR, Settings, get_settings

LOGIN_TIMEOUT_S = 30
MIGRATIONS_DIR = BACKEND_DIR / "sql" / "snowflake"

# The backend turns this on at startup (app/main.py): its sessions then set ABORT_DETACHED_QUERY, so a query whose
# process died without cancelling it (kill -9, a crash) is aborted by Snowflake once the client stops polling,
# instead of running on for minutes. Scripts leave it off: `make train` must survive a laptop that naps.
abort_detached = False

_open_lock = threading.Lock()
_open: "weakref.WeakSet[snowflake.connector.SnowflakeConnection]" = weakref.WeakSet()


class SnowflakeNotConfigured(RuntimeError):
    pass


@dataclass
class FeatureStats:
    calls: int = 0
    errors: int = 0
    total_ms: float = 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0


class CallStats:
    """Thread-safe per-feature call counters (the connector runs in worker threads). `drain()` hands the
    calls recorded since the last drain to the Tiger `snowflake_calls` totals."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_feature: dict[str, FeatureStats] = {}
        self._pending: dict[str, FeatureStats] = {}

    def record(self, feature: str, elapsed_ms: float, ok: bool) -> None:
        with self._lock:
            for book in (self._by_feature, self._pending):
                stats = book.setdefault(feature, FeatureStats())
                stats.calls += 1
                stats.total_ms += elapsed_ms
                if not ok:
                    stats.errors += 1

    def drain(self) -> dict[str, FeatureStats]:
        with self._lock:
            pending, self._pending = self._pending, {}
        return pending

    def restore(self, pending: dict[str, FeatureStats]) -> None:
        """Put drained counts back (the write to Tiger failed) so they go out with the next flush."""
        with self._lock:
            for name, s in pending.items():
                cur = self._pending.setdefault(name, FeatureStats())
                cur.calls += s.calls
                cur.errors += s.errors
                cur.total_ms += s.total_ms

    @contextmanager
    def timed(self, feature: str) -> Iterator[None]:
        start = time.perf_counter()
        ok = False
        try:
            yield
            ok = True
        finally:
            self.record(feature, (time.perf_counter() - start) * 1000, ok)

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                name: {
                    "calls": s.calls,
                    "errors": s.errors,
                    "avg_ms": round(s.avg_ms, 1),
                }
                for name, s in self._by_feature.items()
            }


call_stats = CallStats()


def connect(settings: Settings | None = None, warehouse: str | None = None) -> snowflake.connector.SnowflakeConnection:
    settings = settings or get_settings()
    if not settings.snowflake_configured:
        raise SnowflakeNotConfigured("SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER are not set in .env")
    key_file = settings.snowflake_private_key_file
    if not key_file.is_file():
        raise SnowflakeNotConfigured(f"private key not found at {key_file} (run `make keygen`)")
    with call_stats.timed("connect"):
        conn = snowflake.connector.connect(
            account=settings.snowflake_account,
            user=settings.snowflake_user,
            authenticator="SNOWFLAKE_JWT",
            private_key_file=str(key_file),
            role=settings.snowflake_role,
            warehouse=warehouse or settings.snowflake_warehouse,
            database=settings.snowflake_database,
            login_timeout=LOGIN_TIMEOUT_S,
            application="vigil",
            # Explicit to silence the connector's FutureWarning; we run no async queries, so always log out on close.
            enable_server_session_keep_alive_auto_detection=False,
            session_parameters={"ABORT_DETACHED_QUERY": True} if abort_detached else None,
        )
    with _open_lock:
        _open.add(conn)
    return conn


def open_session_ids() -> list[int]:
    """Snowflake session ids of this process's open connector connections. On shutdown the backend cancels their
    running queries (`snowflake_rest.cancel_sessions`), so a reload doesn't leave DETECT_ANOMALIES or a FORECAST
    training running and queueing the warehouse behind the next process's runs."""
    with _open_lock:
        conns = list(_open)
    return [c.session_id for c in conns if c.session_id and not c.is_closed()]


def close_all() -> None:
    """Log out every open connector connection (after their queries were cancelled)."""
    with _open_lock:
        conns = list(_open)
    for c in conns:
        with contextlib.suppress(Exception):
            c.close()


class SharedConnection:
    """One lazily opened connector connection for a background feature (live forecasts, voice-note PUTs). The
    connector allows concurrent cursors on one connection. A failed call drops the connection and is retried once
    on a fresh login (an idle session expires after a few hours)."""

    def __init__(self, warehouse: str | None = None) -> None:
        self._lock = threading.Lock()
        self._conn: snowflake.connector.SnowflakeConnection | None = None
        self.warehouse = warehouse  # None: SNOWFLAKE_WAREHOUSE

    def get(self) -> snowflake.connector.SnowflakeConnection:
        with self._lock:
            if self._conn is None or self._conn.is_closed():
                self._conn = connect(warehouse=self.warehouse)
            return self._conn

    def reset(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):  # already broken; just drop it
                conn.close()

    def run(self, fn):
        """fn(conn) on the shared connection; on a lost or expired session, once more on a new login. Other errors
        (bad SQL, a missing stage, privileges) are raised at once: a new login would fail the same way."""
        try:
            return fn(self.get())
        except snowflake.connector.errors.Error as exc:
            if not session_lost(exc):
                raise
            self.reset()
            return fn(self.get())


SESSION_EXPIRED_ERRNOS = {390111, 390112, 390114}  # session no longer exists / master token / auth token expired


def session_lost(exc: Exception) -> bool:
    """A connector error a fresh login fixes: the connection dropped or the session or token expired."""
    if isinstance(exc, snowflake.connector.errors.ProgrammingError):
        return getattr(exc, "errno", None) in SESSION_EXPIRED_ERRNOS
    return isinstance(exc, snowflake.connector.errors.OperationalError | snowflake.connector.errors.InterfaceError)


shared = SharedConnection()  # voice-note PUTs


def ml_warehouse() -> str | None:
    """The warehouse for heavy ML (FORECAST training, `make train`): SNOWFLAKE_TRAIN_WAREHOUSE if set, so it never
    queues the interactive Cortex calls (notes, cards, handoff, Ask) on the XSMALL SNOWFLAKE_WAREHOUSE."""
    return get_settings().snowflake_train_warehouse or None


def query(
    conn: snowflake.connector.SnowflakeConnection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] | None = None,
    *,
    feature: str = "sql",
) -> list[tuple]:
    """Run one statement and return all rows, counted under `feature`."""
    with call_stats.timed(feature):
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
        finally:
            cur.close()


def query_dicts(
    conn: snowflake.connector.SnowflakeConnection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] | None = None,
    *,
    feature: str = "sql",
) -> list[dict[str, Any]]:
    """Like `query`, but rows are dicts keyed by column name (handy for SHOW commands)."""
    with call_stats.timed(feature):
        cur = conn.cursor(snowflake.connector.DictCursor)
        try:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
        finally:
            cur.close()


def query_columns(
    conn: snowflake.connector.SnowflakeConnection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] | None = None,
    *,
    feature: str = "sql",
) -> tuple[list[str], list[tuple]]:
    """Run one statement (e.g. a CALL to an ML function) and return (column names, rows)."""
    with call_stats.timed(feature):
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            if not cur.description:
                return [], []
            return [d[0] for d in cur.description], cur.fetchall()
        finally:
            cur.close()


# --------------------------------------------------------------------------- scripts and bulk writes


def script_statements(path: Path) -> list[str]:
    return [s for s, _ in split_statements(io.StringIO(path.read_text()), remove_comments=True) if s.strip()]


def run_script(conn: snowflake.connector.SnowflakeConnection, path: Path, *, feature: str = "migrate") -> int:
    """Run every statement of a SQL file in order; returns the statement count. Each statement is one
    counted call, so a failing statement is reported with its position."""
    statements = script_statements(path)
    for i, stmt in enumerate(statements, 1):
        try:
            query(conn, stmt, feature=feature)
        except snowflake.connector.Error as exc:
            first = " ".join(stmt.split())[:100]
            raise RuntimeError(f"{path.name} statement {i} ({first} …): {exc}") from exc
    return len(statements)


def write_df(
    conn: snowflake.connector.SnowflakeConnection,
    df: pd.DataFrame,
    table: str,
    *,
    schema: str = "CORE",
    variant: Sequence[str] = (),
    feature: str = "write_pandas",
) -> int:
    """Append a DataFrame to an existing table with write_pandas (stage PUT + COPY INTO). Column names must
    match the table's (upper-case); naive datetime64 columns become TIMESTAMP_NTZ.

    `variant` names columns holding JSON text for VARIANT/ARRAY columns: those rows go through a session
    temporary table and `INSERT … SELECT PARSE_JSON(…)`, since a plain COPY would store them as strings."""
    if df.empty:
        return 0
    with call_stats.timed(feature):
        target = table if not variant else f"{table}_STAGE"
        ok, _, nrows, _ = write_pandas(
            conn, df, target, schema=schema, quote_identifiers=False, use_logical_type=True,
            auto_create_table=bool(variant), overwrite=bool(variant), table_type="temporary" if variant else "",
        )  # fmt: skip
        if not ok:
            raise RuntimeError(f"write_pandas into {schema}.{target} reported failure")
        if variant:
            cols = ", ".join(df.columns)
            select = ", ".join(f"PARSE_JSON({c})" if c in variant else c for c in df.columns)
            cur = conn.cursor()
            try:
                cur.execute(f"INSERT INTO {schema}.{table} ({cols}) SELECT {select} FROM {schema}.{target}")
            finally:
                cur.close()
    return int(nrows)
