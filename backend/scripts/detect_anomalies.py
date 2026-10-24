"""`make detect`: run Snowflake ANOMALY_DETECTION on demand over a time range, then summarize the flags.

Default range: last night's scripted window (22:00 → 06:30 facility time). Results replace any earlier ones
for the range in CORE.ANOMALY_RESULTS and Tiger ai_insights. `--summary-only` just prints what is stored.

    uv run python scripts/detect_anomalies.py                       # last night
    uv run python scripts/detect_anomalies.py --from 2026-10-16T05:00Z --to 2026-10-16T13:30Z
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.deep import anomaly
from app.sim.cast import CAST_IDS

METRICS = tuple(anomaly.MODELS)


def _parse(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


async def last_night(conn) -> tuple[datetime, datetime]:
    night = await tiger.get_meta(conn, "scripted_night")
    if not night:
        raise SystemExit("detect: no scripted night in sim_meta — run `make seed` first")
    return datetime.fromisoformat(night["start"]), datetime.fromisoformat(night["end"])


async def summarize(conn, start: datetime, end: datetime, ids=CAST_IDS) -> None:
    """Per resident (default: the SPEC §8 cast): flagged buckets per metric and the first flag time."""
    tz = ZoneInfo(get_settings().facility_tz)
    names = dict(await tiger.fetchall(conn, "SELECT resident_id, name FROM residents"))
    rows = await tiger.fetch_dicts(
        conn,
        """SELECT resident_id, as_of, payload FROM ai_insights
           WHERE kind = 'anomaly' AND (payload->>'bucket')::timestamptz >= %s AND (payload->>'bucket')::timestamptz < %s
           ORDER BY resident_id, as_of""",
        (start, end),
    )
    buckets = {rid: 0 for rid in names}
    flagged: dict[int, dict[str, list]] = {}
    for r in rows:
        buckets[r["resident_id"]] += 1
        for m, v in r["payload"]["metrics"].items():
            if v["severity"] > 0:
                flagged.setdefault(r["resident_id"], {}).setdefault(m, []).append(
                    (datetime.fromisoformat(r["payload"]["bucket"]), v["severity"])
                )
    print(f"\nflags {start.astimezone(tz):%a %H:%M} → {end.astimezone(tz):%a %H:%M} ({tz.key}); "
          f"first flag = bucket start, max = max severity")  # fmt: skip
    print(f"  {'resident':<22}{'buckets':>8}  " + "  ".join(f"{m:<18}" for m in METRICS))
    for rid in ids:
        cells = []
        for m in METRICS:
            hits = flagged.get(rid, {}).get(m, [])
            cells.append(
                f"{len(hits):>2}× {hits[0][0].astimezone(tz):%H:%M} max {max(s for _, s in hits):.2f}" if hits else "–"
            )
        print(f"  {rid:>3} {names.get(rid, '?'):<18}{buckets.get(rid, 0):>8}  " + "  ".join(f"{c:<18}" for c in cells))
    others = {rid: sum(len(v) for v in ms.values()) for rid, ms in flagged.items() if rid not in ids}
    total = sum(buckets.values())
    n_flags = sum(len(v) for ms in flagged.values() for v in ms.values())
    print(f"  all residents: {total:,} resident-buckets, {n_flags} metric flags; outside the cast "
          f"{len(others)} residents with ≥ 1 flag ({sum(others.values())} flags)")  # fmt: skip
    if others:
        top = sorted(others.items(), key=lambda kv: -kv[1])[:5]
        print("  most flagged outside the cast: " + ", ".join(f"{names[r]} ({r}) {n}" for r, n in top))


async def run(start: datetime | None, end: datetime | None, summary_only: bool) -> None:
    async with tiger.connect(application_name="vigil-detect") as conn:
        if start is None or end is None:
            ns, ne = await last_night(conn)
            start, end = start or ns, end or ne
        if not summary_only:
            meta = await anomaly.models_meta(conn)
            if meta is None:
                raise SystemExit("detect: no anomaly models for the current seed — run `make train`")
            train_to = datetime.fromisoformat(meta["train_to"])
            if start < train_to:
                print(f"detect: start moved to the end of the training data ({train_to:%Y-%m-%d %H:%M} UTC)")
                start = train_to
            if not await tiger.try_lock_sync(conn):
                print("detect: waiting for the backend's sync cycle to finish …")
                await tiger.lock_sync(conn)
            try:
                sconn = await asyncio.to_thread(sf.connect)
                try:
                    det = await anomaly.detect_range(conn, sconn, start, end, train_to)
                finally:
                    sconn.close()
            finally:
                await tiger.unlock_sync(conn)
            print(f"detect: {len(det.results):,} results for {start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC in "
                  f"{anomaly.stats.last_seconds:.1f}s · per model " +
                  ", ".join(f"{m} {s:.1f}s" for m, s in det.seconds.items()))  # fmt: skip
            for m, err in det.errors.items():
                print(f"  ✗ {m}: {err}")
        await summarize(conn, start, end)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", type=_parse, help="ISO 8601 (default: last night 22:00)")
    parser.add_argument("--to", dest="end", type=_parse, help="ISO 8601 (default: last night 06:30)")
    parser.add_argument("--summary-only", action="store_true", help="print stored results without detecting")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.tiger_configured or (not args.summary_only and not settings.snowflake_configured):
        print("detect: Tiger and Snowflake must be configured in .env", file=sys.stderr)
        return 1
    asyncio.run(run(args.start, args.end, args.summary_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
