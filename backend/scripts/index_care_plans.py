"""`make care-plans`: generate the care-plan PDFs, upload + parse + chunk them in Snowflake, build the
Cortex Search service (sql/snowflake/003_docs.sql), wait until it serves, and try one REST query.

Also records each resident's stage URI in Tiger `residents.care_plan_uri`.
"""

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

import generate_care_plans

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.db.snowflake_rest import client as rest_client
from app.sim.care_plans import filename

SQL_FILE = sf.MIGRATIONS_DIR / "003_docs.sql"
READY_TIMEOUT_S = 600
PROBES = ((17, "confusion infection escalation code status"), (101, "code status POLST escalation"),
          (120, "fall precautions bed alarm"))  # fmt: skip


def rendered_sql(pdf_dir: Path) -> Path:
    """003_docs.sql with ${CARE_PLANS_DIR} filled in (a temp copy; the checked-in file stays portable)."""
    text = SQL_FILE.read_text().replace("${CARE_PLANS_DIR}", pdf_dir.resolve().as_posix())
    tmp = Path(tempfile.mkdtemp()) / SQL_FILE.name
    tmp.write_text(text)
    return tmp


def wait_ready(conn) -> dict:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        rows = sf.query_dicts(conn, "SHOW CORTEX SEARCH SERVICES LIKE 'CARE_PLAN_SEARCH' IN SCHEMA DOCS",
                              feature="docs.status")  # fmt: skip
        row = {k.lower(): v for k, v in rows[0].items()} if rows else {}
        if str(row.get("serving_state", "")).upper() == "ACTIVE" and str(row.get("indexing_state", "")).upper() in (
            "ACTIVE",
            "RUNNING",
            "",
        ):
            return row
        if time.monotonic() > deadline:
            raise TimeoutError(f"search service not ready after {READY_TIMEOUT_S}s: {row}")
        time.sleep(10)


async def probe() -> None:
    rest = rest_client()
    try:
        for rid, q in PROBES:
            start = time.perf_counter()
            hits = await rest.search_care_plan(rid, q, limit=4, timeout=15)
            print(f"  R-{rid} '{q}' → {len(hits)} chunks in {(time.perf_counter() - start) * 1000:.0f} ms")
            for h in hits[:3]:
                print(f"     [{h['section']}] {' '.join(h['chunk'].split())[:110]}")
    finally:
        await rest.aclose()


async def record_uris(ids: list[int]) -> None:
    async with tiger.connect() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE residents SET care_plan_uri = %s WHERE resident_id = %s",
                [(f"@VIGIL.DOCS.CARE_PLANS/{filename(rid)}", rid) for rid in ids],
            )
        await conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-generate", action="store_true", help="use the PDFs already in data/care_plans")
    args = parser.parse_args()
    if not get_settings().snowflake_configured:
        print("care-plans: SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER are not set in .env", file=sys.stderr)
        return 1
    out = generate_care_plans.OUT_DIR
    if not args.skip_generate:
        paths = generate_care_plans.generate(out)
        print(f"care-plans: wrote {len(paths)} PDFs to {out}")
    ids = sorted(int(p.stem.split("_")[1]) for p in out.glob("resident_*.pdf"))
    conn = sf.connect()
    try:
        for i, stmt in enumerate(sf.script_statements(rendered_sql(out)), 1):
            start = time.perf_counter()
            first = " ".join(stmt.split())[:70]
            rows = sf.query(conn, stmt, feature="docs.index")
            print(f"  ✓ {i:>2}. {first:<72} {(time.perf_counter() - start):>6.1f} s  ({len(rows)} rows)")
        ((docs, empty),) = sf.query(
            conn, "SELECT COUNT(*), COUNT_IF(content IS NULL OR LENGTH(content) < 200) FROM DOCS.CARE_PLAN_DOCS"
        )
        ((chunks, residents, sections),) = sf.query(
            conn, "SELECT COUNT(*), COUNT(DISTINCT resident_id), COUNT(DISTINCT section) FROM DOCS.CARE_PLAN_CHUNKS"
        )
        print(f"care-plans: {docs} parsed docs ({empty} empty) · {chunks} chunks · {residents} residents · "
              f"{sections} section names")  # fmt: skip
        print("care-plans: waiting for DOCS.CARE_PLAN_SEARCH to serve …")
        row = wait_ready(conn)
        print(f"  serving_state={row.get('serving_state')} indexing_state={row.get('indexing_state')}")
    finally:
        conn.close()
    asyncio.run(probe())
    if get_settings().tiger_configured:
        asyncio.run(record_uris(ids))
        print(f"care-plans: residents.care_plan_uri set for {len(ids)} residents in Tiger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
