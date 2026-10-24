"""`make check`: verify Tiger Cloud and Snowflake are reachable and set up. Exit 1 on any FAIL.

Tiger: server + TimescaleDB versions, timescaledb_toolkit (created if missing), a hyperfunction probe.
Snowflake: key-pair login, role/warehouse/database, Cortex models, a one-line AI_COMPLETE.
"""

import asyncio
import sys
import time
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.db import snowflake_sql as sf
from app.db import tiger

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
COLORS = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[90m"}


@dataclass
class Check:
    component: str
    name: str
    status: str
    detail: str = ""


def one_line(exc: BaseException, limit: int = 180) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ms_since(start: float) -> str:
    return f"{(time.perf_counter() - start) * 1000:.0f} ms"


# --------------------------------------------------------------------------- Tiger


async def check_tiger(settings: Settings) -> list[Check]:
    c = "Tiger"
    if not settings.tiger_configured:
        return [Check(c, "config", FAIL, "TIGER_DATABASE_URL is not set in .env")]
    checks: list[Check] = []
    start = time.perf_counter()
    try:
        async with tiger.connect() as conn:
            (pg_version,) = await tiger.fetchone(conn, "SHOW server_version")
            checks.append(Check(c, "connect", PASS, f"PostgreSQL {pg_version.split()[0]} · {ms_since(start)}"))

            row = await tiger.fetchone(conn, "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            checks.append(
                Check(c, "timescaledb", PASS, row[0])
                if row
                else Check(c, "timescaledb", FAIL, "extension not installed on this service")
            )
            checks.append(await _check_toolkit(conn))
            checks.append(await _probe_hyperfunctions(conn))
    except Exception as exc:
        checks.append(Check(c, "connect", FAIL, one_line(exc)))
    return checks


async def _check_toolkit(conn) -> Check:
    c, name = "Tiger", "timescaledb_toolkit"
    sql = "SELECT installed_version, default_version FROM pg_available_extensions WHERE name = %s"
    row = await tiger.fetchone(conn, sql, (name,))
    if row is None:
        return Check(c, name, WARN, "not available on this plan (hyperfunctions will use plain-SQL fallbacks)")
    if row[0]:
        return Check(c, name, PASS, row[0])
    try:
        await tiger.execute(conn, "CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit")
    except Exception as exc:
        return Check(c, name, WARN, f"CREATE EXTENSION failed (plain-SQL fallbacks): {one_line(exc)}")
    row = await tiger.fetchone(conn, sql, (name,))
    return Check(c, name, PASS, f"{row[0]} (extension created now)")


async def _probe_hyperfunctions(conn) -> Check:
    c, name = "Tiger", "hyperfunctions"
    try:
        (p50,) = await tiger.fetchone(
            conn,
            "SELECT approx_percentile(0.5, percentile_agg(x::float8)) FROM generate_series(1, 101) AS x",
        )
        return Check(c, name, PASS, f"percentile_agg/approx_percentile ok (p50≈{p50:.0f})")
    except Exception as exc:
        return Check(c, name, WARN, one_line(exc))


# ----------------------------------------------------------------------- Snowflake

LOGIN_HINTS = {
    "jwt": (
        "SNOWFLAKE_USER must be the user's LOGIN_NAME (often an email), and its RSA_PUBLIC_KEY_FP must match "
        "`make keygen` — check both with DESC USER"
    ),
    "role": "role missing or not granted to this user — run backend/sql/snowflake/001_setup.sql in Snowsight",
    "does not exist": "check SNOWFLAKE_ACCOUNT (orgname-accountname) and SNOWFLAKE_USER (LOGIN_NAME)",
    "404 not found": "account not found — SNOWFLAKE_ACCOUNT must be orgname-accountname (Snowsight → account menu)",
}


def check_snowflake(settings: Settings) -> list[Check]:
    c = "Snowflake"
    if not settings.snowflake_configured:
        return [Check(c, "config", FAIL, "SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER are not set in .env")]
    if not settings.snowflake_private_key_file.is_file():
        return [
            Check(c, "config", FAIL, f"no private key at {settings.snowflake_private_key_file} — run `make keygen`")
        ]

    start = time.perf_counter()
    try:
        conn = sf.connect(settings)
    except Exception as exc:
        msg = one_line(exc)
        hint = next((h for key, h in LOGIN_HINTS.items() if key in msg.lower()), "")
        return [Check(c, "key-pair login", FAIL, f"{msg}{' → ' + hint if hint else ''}")]

    checks: list[Check] = []
    try:
        _run_snowflake_checks(conn, settings, checks, start)
    except Exception as exc:
        checks.append(Check(c, "checks", FAIL, f"aborted: {one_line(exc)}"))
    finally:
        conn.close()
    return checks


def _run_snowflake_checks(conn, settings: Settings, checks: list[Check], start: float) -> None:
    c = "Snowflake"
    user, role, warehouse, database, region = sf.query(
        conn,
        "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_REGION()",
        feature="check",
    )[0]
    checks.append(Check(c, "key-pair login", PASS, f"user {user} · region {region} · {ms_since(start)}"))
    checks.append(
        Check(c, "role", PASS, role)
        if (role or "").upper() == settings.snowflake_role.upper()
        else Check(c, "role", FAIL, f"expected {settings.snowflake_role}, got {role}")
    )
    checks.append(_check_warehouse(conn, settings, warehouse))
    checks.append(_check_database(conn, settings, database))
    checks.append(_check_cross_region(conn))
    checks.append(_check_models(conn, settings))
    checks.append(_check_ai_complete(conn, settings))
    checks.append(
        _probe_show(
            conn, "ML functions", f"SHOW SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA {settings.snowflake_database}.ML"
        )
    )
    checks.append(
        _probe_show(conn, "Cortex Search", f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {settings.snowflake_database}.DOCS")
    )


def _check_warehouse(conn, settings: Settings, current: str | None) -> Check:
    c, name = "Snowflake", "warehouse"
    if not current:
        return Check(c, name, FAIL, f"{settings.snowflake_warehouse} not usable by this role — run 001_setup.sql")
    rows = sf.query_dicts(conn, "SHOW WAREHOUSES LIKE %s", (current,), feature="check")
    if not rows:
        return Check(c, name, PASS, current)
    wh = rows[0]
    return Check(
        c, name, PASS, f"{current} · {wh.get('size')} · auto_suspend {wh.get('auto_suspend')}s · {wh.get('state')}"
    )


def _check_database(conn, settings: Settings, current: str | None) -> Check:
    c, name = "Snowflake", "database"
    if not current:
        return Check(c, name, FAIL, f"{settings.snowflake_database} not found/usable — run 001_setup.sql")
    rows = sf.query_dicts(conn, f"SHOW SCHEMAS IN DATABASE {current}", feature="check")
    present = {r["name"].upper() for r in rows}
    missing = [s for s in ("CORE", "ML", "DOCS") if s not in present]
    if missing:
        return Check(c, name, FAIL, f"{current} missing schemas: {', '.join(missing)}")
    return Check(c, name, PASS, f"{current} · schemas CORE, ML, DOCS")


def _check_cross_region(conn) -> Check:
    c, name = "Snowflake", "cross-region"
    try:
        rows = sf.query_dicts(conn, "SHOW PARAMETERS LIKE 'CORTEX_ENABLED_CROSS_REGION' IN ACCOUNT", feature="check")
    except Exception as exc:
        return Check(c, name, SKIP, f"cannot read parameter: {one_line(exc, 80)}")
    value = rows[0]["value"] if rows else "unknown"
    if value.upper() == "DISABLED":
        return Check(c, name, WARN, "DISABLED — Claude needs cross-region inference in US West 2")
    return Check(c, name, PASS, value)


def _check_models(conn, settings: Settings) -> Check:
    c, name = "Snowflake", "Cortex models"
    try:
        rows = sf.query_dicts(
            conn, "SHOW CORTEX BASE MODELS LIKE 'claude%' IN SCHEMA SNOWFLAKE.MODELS", feature="check"
        )
    except Exception as exc:
        return Check(c, name, WARN, f"cannot list models: {one_line(exc, 100)}")
    names = sorted(r["name"].lower() for r in rows)
    if not names:
        return Check(c, name, WARN, "no Claude models visible to this role (listing is RBAC-filtered)")
    listed = ", ".join(names)
    if settings.cortex_model.lower() in names:
        return Check(c, name, PASS, f"CORTEX_MODEL={settings.cortex_model} available · {listed}")
    return Check(c, name, WARN, f"CORTEX_MODEL={settings.cortex_model} not listed · available: {listed}")


def _check_ai_complete(conn, settings: Settings) -> Check:
    c, name = "Snowflake", "AI_COMPLETE"
    start = time.perf_counter()
    try:
        ((reply,),) = sf.query(
            conn,
            "SELECT AI_COMPLETE(%s, %s)",
            (settings.cortex_model, "Reply with exactly one word: ready"),
            feature="check",
        )
    except Exception as exc:
        if "not available for trial accounts" in str(exc):
            return Check(
                c, name, FAIL, "Cortex AI is blocked on this trial account → add a payment method or use the MLH trial"
            )
        return Check(c, name, FAIL, f"{settings.cortex_model}: {one_line(exc)}")
    reply = " ".join(str(reply).split()).strip('"')[:60]  # AI_COMPLETE returns a JSON-quoted string
    return Check(c, name, PASS, f'{settings.cortex_model} → "{reply}" · {ms_since(start)}')


def _probe_show(conn, name: str, sql: str) -> Check:
    try:
        rows = sf.query(conn, sql, feature="check")
        # Visibility only: real use (training, search) is first exercised in Phases 5-6.
        return Check("Snowflake", name, PASS, f"visible ({len(rows)} existing; usage not exercised yet)")
    except Exception as exc:
        return Check("Snowflake", name, WARN, one_line(exc, 120))


# --------------------------------------------------------------------------- report


def print_table(checks: list[Check]) -> None:
    color = sys.stdout.isatty()
    w0 = max(9, *(len(k.component) for k in checks))
    w1 = max(5, *(len(k.name) for k in checks))
    print(f"\n{'COMPONENT':<{w0}}  {'CHECK':<{w1}}  STATUS  DETAIL")
    print(f"{'-' * w0}  {'-' * w1}  ------  {'-' * 40}")
    for k in checks:
        status = f"{COLORS[k.status]}{k.status}\033[0m" if color else k.status
        print(f"{k.component:<{w0}}  {k.name:<{w1}}  {status}    {k.detail}")


def summarize(checks: list[Check]) -> dict[str, str]:
    summary: dict[str, str] = {}
    for k in checks:
        if k.status == FAIL or summary.get(k.component) == FAIL:
            summary[k.component] = FAIL
        else:
            summary.setdefault(k.component, PASS)
    return summary


def main() -> int:
    settings = get_settings()
    checks = asyncio.run(check_tiger(settings)) + check_snowflake(settings)
    print_table(checks)
    summary = summarize(checks)
    print("\nOverall: " + " · ".join(f"{comp} {status}" for comp, status in summary.items()) + "\n")
    return 1 if FAIL in summary.values() else 0


if __name__ == "__main__":
    sys.exit(main())
