"""Apply backend/sql/tiger/*.sql to Tiger in filename order. Implemented in Phase 2 (with the schema)."""

import sys

from app.config import BACKEND_DIR


def main() -> int:
    files = sorted((BACKEND_DIR / "sql" / "tiger").glob("*.sql"))
    print(f"migrate: stub — {len(files)} migration file(s) in backend/sql/tiger. Implemented in Phase 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
