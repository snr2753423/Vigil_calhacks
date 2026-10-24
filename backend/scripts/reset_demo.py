"""Destructive resets live here (AGENTS.md rule 8). The full demo reset arrives in Phase 8.

`truncate_history()` is what `make seed` uses to clear Tiger before regenerating synthetic history;
`truncate_snowflake()` empties the VIGIL.CORE copies before the seed's bulk load (`make sf-load` too).
The anomaly models in VIGIL.ML are kept; `make train` replaces them.
"""

import sys

from app.db import snowflake_sql as sf
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


SNOWFLAKE_TABLES = (
    "RESIDENTS",
    "STAFF",
    "VITALS_15M",
    "EVENTS",
    "CHECKS",
    "NURSE_NOTES",
    "ALERTS",
    "ANOMALY_RESULTS",
    "FORECAST_RESULTS",
    "AI_ASSESSMENTS",
    "HANDOFFS",
)


async def truncate_history(conn) -> None:
    """Empty every history and reference table (continuous aggregates follow on their next refresh). The
    anomaly-model metadata goes too: models trained on the old history must be retrained (`make train`)."""
    await tiger.execute(conn, f"TRUNCATE {', '.join(HISTORY_TABLES)}")
    await tiger.execute(conn, "DELETE FROM sim_meta WHERE key = 'anomaly_models'")


def truncate_snowflake(sconn) -> None:
    """Empty the VIGIL.CORE tables the Tiger → Snowflake sync and the deep tier write."""
    for table in SNOWFLAKE_TABLES:
        sf.query(sconn, f"TRUNCATE TABLE IF EXISTS CORE.{table}", feature="reset")


def main() -> int:
    print("reset_demo: the full demo reset is implemented in Phase 8; `make seed` uses truncate_history().")
    return 0


if __name__ == "__main__":
    sys.exit(main())
