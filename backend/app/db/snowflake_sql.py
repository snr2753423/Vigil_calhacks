"""The only module that runs SQL on Snowflake (Python connector, key-pair auth). See AGENTS.md rule 3.

Every call is counted with its latency under a feature name (`sync.vitals_15m`, `anomaly.detect.hr`, …);
`/api/stats/snowflake` reads `call_stats`. `snowflake_rest.py` records its calls here too.
"""

import io
import threading
import time
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
    """Thread-safe per-feature call counters (the connector runs in worker threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_feature: dict[str, FeatureStats] = {}

    def record(self, feature: str, elapsed_ms: float, ok: bool) -> None:
        with self._lock:
            stats = self._by_feature.setdefault(feature, FeatureStats())
            stats.calls += 1
            stats.total_ms += elapsed_ms
            if not ok:
                stats.errors += 1

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


def connect(settings: Settings | None = None) -> snowflake.connector.SnowflakeConnection:
    settings = settings or get_settings()
    if not settings.snowflake_configured:
        raise SnowflakeNotConfigured("SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER are not set in .env")
    key_file = settings.snowflake_private_key_file
    if not key_file.is_file():
        raise SnowflakeNotConfigured(f"private key not found at {key_file} (run `make keygen`)")
    with call_stats.timed("connect"):
        return snowflake.connector.connect(
            account=settings.snowflake_account,
            user=settings.snowflake_user,
            authenticator="SNOWFLAKE_JWT",
            private_key_file=str(key_file),
            role=settings.snowflake_role,
            warehouse=settings.snowflake_warehouse,
            database=settings.snowflake_database,
            login_timeout=LOGIN_TIMEOUT_S,
            application="vigil",
            # Explicit to silence the connector's FutureWarning; we run no async queries, so always log out on close.
            enable_server_session_keep_alive_auto_detection=False,
        )


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
