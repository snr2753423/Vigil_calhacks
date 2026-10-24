"""`make migrate-sf`: create the VIGIL.CORE tables in Snowflake (backend/sql/snowflake, idempotent).

001_setup.sql is the one-time ACCOUNTADMIN bootstrap you paste into Snowsight (`make keygen` prints it);
pass --include-setup to run it from here too (your login must have ACCOUNTADMIN). 004_models.sql trains
the anomaly models and is run by `make train` instead; 003_docs.sql needs the care-plan PDFs and is run by
`make care-plans`.
"""

import argparse
import sys
import time

from app.config import get_settings
from app.db import snowflake_sql as sf

SETUP_FILE = "001_setup.sql"
APPLY_FILES = ("002_tables.sql", "005_stages.sql")  # later phases add their DDL here


def run(include_setup: bool) -> None:
    names = ((SETUP_FILE,) if include_setup else ()) + APPLY_FILES
    files = [sf.MIGRATIONS_DIR / n for n in names]
    print(f"migrate-sf: {len(files)} file(s) from backend/sql/snowflake")
    conn = sf.connect()
    try:
        for path in files:
            start = time.perf_counter()
            n = sf.run_script(conn, path)
            print(f"  ✓ {path.name:<20} {n:>3} statements  {(time.perf_counter() - start) * 1000:>7.0f} ms")
        if include_setup:  # 001 ends as VIGIL_ROLE, but be explicit for the rest of the session
            sf.query(conn, f"USE ROLE {get_settings().snowflake_role}")
        tables = sf.query(conn, "SHOW TERSE TABLES IN SCHEMA CORE", feature="migrate")
        print(f"  CORE tables: {', '.join(sorted(r[1] for r in tables))}")
    finally:
        conn.close()
    print("migrate-sf: done")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--include-setup", action="store_true", help=f"also run {SETUP_FILE} (needs ACCOUNTADMIN)")
    args = parser.parse_args()
    if not get_settings().snowflake_configured:
        print("migrate-sf: SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER are not set in .env", file=sys.stderr)
        return 1
    try:
        run(args.include_setup)
    except Exception as exc:
        print(f"migrate-sf: FAILED — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
