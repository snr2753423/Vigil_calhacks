"""POST /api/bench/{name}: live benchmarks for /under-the-hood (SPEC §13).

`hourly_hr`: "7-day hourly HR for all 120 residents", once from raw `vitals` rows and once from the `vitals_1h`
continuous aggregate, timed back to back on the same connection.
"""

import time

import psycopg
from fastapi import APIRouter, HTTPException

from app.api.deps import TigerConn
from app.db import tiger

router = APIRouter(prefix="/api/bench", tags=["stats"])

TITLES = {"hourly_hr": "7-day hourly HR for all 120 residents"}


@router.post("/{name}")
async def bench(name: str, conn: TigerConn) -> dict:
    if name not in tiger.BENCH_QUERIES:
        raise HTTPException(404, f"unknown benchmark {name!r}; try {', '.join(tiger.BENCH_QUERIES)}")
    t0 = time.perf_counter()
    try:
        res = await tiger.run_bench(conn, name)
    except psycopg.errors.QueryCanceled as exc:
        raise HTTPException(504, f"benchmark exceeded {tiger.BENCH_TIMEOUT_MS / 1000:.0f} s") from exc
    raw, agg = res.get("raw"), res.get("vitals_1h")
    return {
        "name": name,
        "title": TITLES.get(name, name),
        "results": res,
        "speedup": round(raw["ms"] / agg["ms"], 1) if raw and agg and agg["ms"] else None,
        "total_ms": round((time.perf_counter() - t0) * 1000, 1),
        "sql": tiger.BENCH_QUERIES[name],
    }
