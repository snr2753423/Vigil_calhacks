"""The only module that runs SQL on Snowflake (Python connector, key-pair auth). See AGENTS.md rule 3.

Every call is counted with its latency under a feature name; `/api/stats/snowflake`
(Phase 5+) reads `call_stats`. `snowflake_rest.py` records its calls here too.
"""

import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import snowflake.connector

from app.config import Settings, get_settings

LOGIN_TIMEOUT_S = 30


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
