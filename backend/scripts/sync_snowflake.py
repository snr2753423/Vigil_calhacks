"""`make sf-load`: replace the Snowflake copy of Tiger history (VIGIL.CORE) with a fresh bulk load.

`make seed` runs this at its end; run it yourself if that step failed or was skipped. It ensures the CORE
tables exist, empties them (scripts/reset_demo.py), then loads residents, staff, every closed vitals_15m
bucket and every event, and resets the sync watermarks. A running backend's sync loop stands by meanwhile
and continues incrementally afterwards. `--once` instead runs one incremental sync (what the loop does).
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from reset_demo import truncate_snowflake

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.deep import sync


async def load_snowflake(tconn, say: Callable[[str], None] = print) -> dict[str, int]:
    """Bulk load; the caller holds the sync lock on `tconn`."""
    sconn = await asyncio.to_thread(sf.connect)
    try:
        await asyncio.to_thread(sf.run_script, sconn, sf.MIGRATIONS_DIR / "002_tables.sql")
        await asyncio.to_thread(truncate_snowflake, sconn)
        return await sync.bulk_load(tconn, sconn, datetime.now(UTC), say)
    finally:
        sconn.close()


async def run(once: bool) -> None:
    async with tiger.connect(application_name="vigil-sf-load") as conn:
        if not await tiger.try_lock_sync(conn):
            print("sf-load: waiting for the backend's sync cycle to finish …")
            await tiger.lock_sync(conn)
        try:
            if once:
                sconn = await asyncio.to_thread(sf.connect)
                try:
                    res = await sync.sync_once(conn, sconn, datetime.now(UTC), dedupe=True)
                finally:
                    sconn.close()
                print("sf-load: no bulk load yet — run without --once" if res is None else f"sf-load: {res}")
                return
            print("sf-load: bulk load → VIGIL.CORE")
            await load_snowflake(conn)
        finally:
            await tiger.unlock_sync(conn)
    print("sf-load: done — next: `make train`")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="one incremental sync instead of a bulk load")
    args = parser.parse_args()
    settings = get_settings()
    if not (settings.snowflake_configured and settings.tiger_configured):
        print("sf-load: Tiger and Snowflake must both be configured in .env", file=sys.stderr)
        return 1
    try:
        try:
            asyncio.run(run(args.once))
        finally:
            asyncio.run(sync.flush_calls())  # → Tiger snowflake_calls (/under-the-hood)
    except Exception as exc:
        print(f"sf-load: FAILED — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
