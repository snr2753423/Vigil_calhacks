"""Destructive Tiger resets live here (AGENTS.md rule 8). The full demo reset arrives in Phase 8.

`truncate_history()` is what `make seed` uses to clear Tiger before regenerating synthetic history.
"""

import sys

from app.db import tiger

HISTORY_TABLES = (
    "vitals",
    "events",
    "priority_snapshots",
    "resident_baselines",
    "ai_insights",
    "sync_state",
    "devices",
    "residents",
    "staff",
)


async def truncate_history(conn) -> None:
    """Empty every history and reference table (continuous aggregates follow on their next refresh)."""
    await tiger.execute(conn, f"TRUNCATE {', '.join(HISTORY_TABLES)}")


def main() -> int:
    print("reset_demo: the full demo reset is implemented in Phase 8; `make seed` uses truncate_history().")
    return 0


if __name__ == "__main__":
    sys.exit(main())
