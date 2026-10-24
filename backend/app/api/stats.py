"""Proof panels for /under-the-hood (SPEC §13, AGENTS.md rule 3). Every number comes from a query, not a constant.

* `GET /api/stats/tiger`: ingest rate, row counts (`approximate_row_count`), columnstore compression, chunks,
  continuous aggregates and job status, straight from the TimescaleDB catalog in one pipelined round trip.
* `GET /api/stats/snowflake`: every Snowflake API surface Vigil uses with call counts and average latency (this
  process and cumulative across processes, from Tiger `snowflake_calls`), the sync, the anomaly models and the
  newest detection run, plus an inventory read from Snowflake itself (models, result series, care-plan chunks,
  the Cortex Search service; cached 5 min). Works with Tiger or Snowflake down: whatever can't be read is null.
* `GET /api/stats/outcome`: the replay night's fixed-threshold alarms vs Vigil alerts, and Alvarez's lead time.
"""

import asyncio
import time

import psycopg
from fastapi import APIRouter

from app.api.deps import facility_tz
from app.config import get_settings
from app.db import snowflake_rest, tiger
from app.db import snowflake_sql as sf
from app.deep import anomaly, assess, sync
from app.devices.ingest import ingest
from app.sim import live_loop, outcome

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _err(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:200] or type(exc).__name__


# --------------------------------------------------------------------------- Tiger


@router.get("/tiger")
async def tiger_stats() -> dict:
    t0 = time.perf_counter()
    try:
        async with tiger.pooled() as conn:
            q = await tiger.tiger_stats(conn)
    except (tiger.TigerNotConfigured, psycopg.Error) as exc:
        return {"ok": False, "error": _err(exc)}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    ingest_rows = sum(r["n"] for r in q["ingest"])
    cs = q["columnstore"][0] if q["columnstore"] else {}
    before, after = cs.get("before_bytes") or 0, cs.get("after_bytes") or 0
    live = live_loop.live
    return {
        "ok": True,
        "query_ms": ms,
        "versions": {r["extname"]: r["extversion"] for r in q["extensions"]} | q["server"][0],
        "ingest": {
            "rows_last_60s": ingest_rows,
            "rows_per_s": round(ingest_rows / 60, 1),
            "by_source": [
                {
                    "source": {0: "simulator", 1: "Polar band", 2: "iPhone bed sensor"}.get(
                        r["source"], str(r["source"])
                    ),
                    "rows": r["n"],
                    "newest": r["newest"],
                }
                for r in q["ingest"]
            ],  # fmt: skip
            "live_loop": live.stats.to_dict() if live else None,
            "devices": ingest.stats.to_dict(),
        },
        "rows": q["rows"][0] if q["rows"] else None,
        "compression": {
            "chunks": cs.get("total_chunks"),
            "compressed_chunks": cs.get("number_compressed_chunks"),
            "before_bytes": before,
            "after_bytes": after,
            "ratio": round(before / after, 1) if after else None,
            "hypertable_bytes": cs.get("size_bytes"),
        },
        "chunks": q["chunks"],
        "caggs": q["caggs"],
        "jobs": q["jobs"],
    }


# --------------------------------------------------------------------------- Snowflake

# (surface key, label, endpoint, predicate on the feature name). Order = display order.
SURFACES = (
    ("sql_api", "SQL REST API", "POST /api/v2/statements", lambda f: f.startswith("rest.sql_api")),
    ("cortex_search", "Cortex Search REST", "POST /api/v2/databases/VIGIL/schemas/DOCS/cortex-search-services/"
     "CARE_PLAN_SEARCH:query", lambda f: f == "rest.cortex_search"),
    ("inference", "Cortex inference REST", "POST /api/v2/cortex/inference:complete",
     lambda f: f == "rest.inference_complete"),
    ("analyst", "Cortex Analyst REST", "POST /api/v2/cortex/analyst/message", lambda f: f == "rest.cortex_analyst"),
    ("connector", "Python connector (SQL)", "snowflake-connector-python, key-pair JWT", lambda f: not f.startswith("rest.")),
)  # fmt: skip
# What each feature does (prefix match, longest first).
FEATURE_TEXT = {
    "rest.sql_api.cortex_complete": "SNOWFLAKE.CORTEX.COMPLETE (Claude): AI assessment cards",
    "rest.sql_api.store_assessment": "INSERT into CORE.AI_ASSESSMENTS",
    "rest.sql_api.inventory": "this panel's inventory (SHOW models / services, counts)",
    "rest.cortex_search": "care-plan chunks for each AI card (filtered by resident)",
    "rest.inference_complete": "tried first for each card; refused for this account (403), falls back to the SQL API",
    "rest.cortex_analyst": "Ask the facility (Phase 9)",
    "anomaly.detect": "CALL ML.AD_*!DETECT_ANOMALIES (SNOWFLAKE.ML.ANOMALY_DETECTION)",
    "anomaly.train": "CREATE SNOWFLAKE.ML.ANOMALY_DETECTION (training)",
    "anomaly.store": "write CORE.ANOMALY_RESULTS",
    "sync.": "Tiger vitals_15m + events → CORE (write_pandas), every minute",
    "bulk.": "seed bulk load Tiger → CORE (write_pandas)",
    "docs.": "care plans: PUT, AI_PARSE_DOCUMENT, SPLIT_TEXT_RECURSIVE_CHARACTER, Cortex Search build",
    "connect": "key-pair login",
    "check": "make check probes (AI_COMPLETE, SHOW CORTEX BASE MODELS, …)",
    "migrate": "schema migrations",
    "reset": "reset_demo truncation",
}
INVENTORY_TTL_S = 300
INVENTORY_RETRY_S = 60  # after a failure (e.g. the warehouse busy with detection), don't ask again sooner
_inventory: dict = {"at": 0.0, "data": None, "failed_at": 0.0, "error": None}


def feature_text(name: str) -> str:
    for prefix in sorted(FEATURE_TEXT, key=len, reverse=True):
        if name.startswith(prefix):
            return FEATURE_TEXT[prefix]
    return ""


def group_surfaces(process: dict[str, dict], cumulative: dict[str, dict]) -> list[dict]:
    """Features grouped by API surface, each with this process's and the cumulative counts."""
    out = []
    for key, label, endpoint, match in SURFACES:
        names = sorted(n for n in set(process) | set(cumulative) if match(n))
        feats = []
        for n in names:
            p, c = process.get(n) or {}, cumulative.get(n) or {}
            feats.append({
                "feature": n, "what": feature_text(n),
                # cumulative includes this process once flushed; if Tiger is down, this process's counts still show
                "calls": max(int(c.get("calls") or 0), int(p.get("calls") or 0)),
                "errors": max(int(c.get("errors") or 0), int(p.get("errors") or 0)),
                "avg_ms": c.get("avg_ms") if c else p.get("avg_ms"), "process_calls": int(p.get("calls") or 0), "process_avg_ms": p.get("avg_ms"),
            })  # fmt: skip
        out.append({
            "key": key, "label": label, "endpoint": endpoint, "features": feats,
            "calls": sum(f["calls"] for f in feats), "errors": sum(f["errors"] for f in feats),
            "process_calls": sum(f["process_calls"] for f in feats),
        })  # fmt: skip
    return out


async def snowflake_inventory(force: bool = False) -> dict:
    """What lives in Snowflake right now, read through the SQL REST API (cached INVENTORY_TTL_S)."""
    now = time.monotonic()
    if not force and _inventory["data"] is not None and now - _inventory["at"] < INVENTORY_TTL_S:
        return _inventory["data"] | {"cached": True}
    if not force and _inventory["error"] and now - _inventory["failed_at"] < INVENTORY_RETRY_S:
        stale = _inventory["data"] or {}
        return stale | {"cached": True, "errors": [f"{_inventory['error']} (retrying within a minute)"]}
    rest = snowflake_rest.client()
    t0 = time.perf_counter()
    feature = "sql_api.inventory"
    models, services, counts = await asyncio.gather(
        rest.sql_dicts("SHOW SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA VIGIL.ML", feature=feature),
        rest.sql_dicts("SHOW CORTEX SEARCH SERVICES IN SCHEMA VIGIL.DOCS", feature=feature),
        rest.sql_dicts(
            """SELECT (SELECT COUNT(*) FROM VIGIL.CORE.ANOMALY_RESULTS) AS anomaly_rows,
                      (SELECT COUNT(DISTINCT RESIDENT_ID) FROM VIGIL.CORE.ANOMALY_RESULTS) AS anomaly_series,
                      (SELECT MAX(DETECTED_AT) FROM VIGIL.CORE.ANOMALY_RESULTS) AS last_detected,
                      (SELECT COUNT(*) FROM VIGIL.CORE.VITALS_15M) AS vitals_15m_rows,
                      (SELECT COUNT(*) FROM VIGIL.CORE.EVENTS) AS event_rows,
                      (SELECT COUNT(*) FROM VIGIL.CORE.AI_ASSESSMENTS) AS assessments,
                      (SELECT COUNT(*) FROM VIGIL.DOCS.CARE_PLAN_DOCS) AS care_plan_docs,
                      (SELECT COUNT(*) FROM VIGIL.DOCS.CARE_PLAN_CHUNKS) AS care_plan_chunks,
                      (SELECT COUNT(DISTINCT RESIDENT_ID) FROM VIGIL.DOCS.CARE_PLAN_CHUNKS) AS care_plan_residents""",
            feature=feature,
        ),
        return_exceptions=True,
    )

    def rows(r):
        return None if isinstance(r, BaseException) else r

    errors = [_err(r) for r in (models, services, counts) if isinstance(r, BaseException)]
    c = (rows(counts) or [{}])[0]
    data = {
        "ms": round((time.perf_counter() - t0) * 1000),
        "models": [{"name": m.get("name"), "created_on": m.get("created_on")} for m in rows(models) or []],
        "search_services": [
            {
                k: s.get(k)
                for k in (
                    "name",
                    "search_column",
                    "attribute_columns",
                    "indexing_state",
                    "serving_state",
                    "source_data_num_rows",
                )
            }
            for s in rows(services) or []
        ],  # fmt: skip
        "counts": {k.lower(): (int(v) if v is not None and str(v).isdigit() else v) for k, v in c.items()},
        "errors": errors,
    }
    if errors and not (rows(models) or rows(counts)):
        _inventory.update(failed_at=time.monotonic(), error=errors[0])
    else:
        _inventory.update(at=time.monotonic(), data=data, error=None)
    return data | {"cached": False}


@router.get("/snowflake")
async def snowflake_stats(inventory: bool = True, refresh: bool = False) -> dict:
    calls = sf.call_stats.snapshot()
    settings = get_settings()
    out: dict = {
        "configured": settings.snowflake_configured,
        "model": settings.cortex_model,
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
        "last_detection": None,
        "cumulative": None,
        "surfaces": None,
        "inventory": None,
    }
    cumulative: dict[str, dict] = {}
    try:
        async with tiger.pooled() as conn:
            await sync.flush_calls(conn)  # so the cumulative totals include this process's latest calls
            out["sync_state"] = await tiger.get_sync_state(conn)
            out["models"] = await anomaly.models_meta(conn)
            out["insights"] = await tiger.insight_counts(conn)
            out["last_detection"] = await tiger.get_meta(conn, anomaly.LAST_RUN_KEY)
            for r in await tiger.snowflake_call_totals(conn):
                cumulative[r["feature"]] = {
                    "calls": r["calls"], "errors": r["errors"], "first_at": r["first_at"], "last_at": r["last_at"],
                    "avg_ms": round(r["total_ms"] / r["calls"], 1) if r["calls"] else None,
                }  # fmt: skip
            out["cumulative"] = {
                "since": min((r["first_at"] for r in cumulative.values()), default=None),
                "calls": sum(int(r["calls"]) for r in cumulative.values()),
                "errors": sum(int(r["errors"]) for r in cumulative.values()),
            }
    except (tiger.TigerNotConfigured, psycopg.Error) as exc:
        out["tiger_error"] = _err(exc)
    out["surfaces"] = group_surfaces(calls, cumulative)
    if inventory and settings.snowflake_configured:
        try:
            out["inventory"] = await asyncio.wait_for(snowflake_inventory(force=refresh), 25)
        except Exception as exc:  # Snowflake down or slow: the rest of the panel still renders
            _inventory.update(failed_at=time.monotonic(), error=_err(exc))
            out["inventory"] = {"errors": [_err(exc)]}
    return out


# --------------------------------------------------------------------------- outcome (replay night)


@router.get("/outcome")
async def outcome_stats() -> dict:
    t0 = time.perf_counter()
    try:
        async with tiger.pooled() as conn:
            res = await outcome.outcome(conn, facility_tz())
    except (tiger.TigerNotConfigured, psycopg.Error) as exc:
        return {"prepared": False, "error": _err(exc)}
    return res | {"query_ms": round((time.perf_counter() - t0) * 1000, 1)}
