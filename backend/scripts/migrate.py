"""`make migrate`: apply backend/sql/tiger/*.sql to Tiger in filename order (all files are idempotent)."""

import asyncio
import sys

from app.config import get_settings
from app.db import tiger


async def run() -> None:
    files = tiger.migration_files()
    print(f"migrate: {len(files)} file(s) from backend/sql/tiger")
    async with tiger.connect() as conn:
        for result in await tiger.apply_migrations(conn, files):
            print(f"  ✓ {result.file:<20} {result.statements:>3} statements  {result.ms:>7.0f} ms")
        # The baseline job reads the facility time zone from here.
        await tiger.set_meta(conn, "facility", {"tz": get_settings().facility_tz}, merge=True)
    print("migrate: done")


def main() -> int:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"migrate: FAILED — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
