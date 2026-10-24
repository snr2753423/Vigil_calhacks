"""`make prepare`: precompute last night's deep-tier results so replay never waits on Snowflake (SPEC §4).

Anomaly detection over the night (skipped if already done with the current models), the gated pass through the
live evaluator (priority_snapshots + alerts + the cast's ranks), the forecast hook (Phase 9), and Snowflake
Cortex cards at every bookmark and escalation. The same thing runs from /admin → Prepare replay.

    uv run python scripts/prepare_replay.py                  # detect if needed, keep cached cards
    uv run python scripts/prepare_replay.py --force-detect   # re-run ANOMALY_DETECTION over the night
    uv run python scripts/prepare_replay.py --force-cards    # regenerate every Cortex card
    uv run python scripts/prepare_replay.py --no-cards       # detection + gated pass only
"""

import argparse
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import snowflake_rest, tiger
from app.deep import sync
from app.sim import prepare
from app.sim.cast import CAST_IDS


def summarize(p: dict) -> None:
    tz = ZoneInfo(get_settings().facility_tz)

    def hhmm(ts: str) -> str:
        return datetime.fromisoformat(ts).astimezone(tz).strftime("%H:%M")

    print("\ncast through the night (tier · score · rank at each change):")
    for rid in CAST_IDS:
        rows = p["cast"].get(str(rid), [])
        night = [r for r in rows if r[0] >= p["start"]]
        cells = " → ".join(f"{hhmm(t)} {tier[0]}{score} #{rank}" for t, tier, score, rank in night[:14])
        more = f" … (+{len(night) - 14})" if len(night) > 14 else ""
        print(f"  R-{rid:<3} {cells}{more}")
    alerts = p["alerts"]
    print(f"\nVigil alerts on the night: {len(alerts)} for {len({a['resident_id'] for a in alerts})} residents")
    for a in alerts[:40]:
        print(f"  {hhmm(a['ts'])} R-{a['resident_id']:<3} {a['from_tier']}→{a['tier']} {a['score']} #{a['rank']} "
              f"{a['rule'] or ''} · {'; '.join(a['factors'][:2])}")  # fmt: skip
    print("\ncards:")
    for c in p["assessments"]:
        print(f"  {hhmm(c['as_of'])} R-{c['resident_id']:<3} {c['trigger']:<10} {c.get('label', ''):<24} {c['status']:<7} "
              f"{c.get('headline') or c.get('error') or ''}")  # fmt: skip


async def run(args: argparse.Namespace) -> int:
    try:
        p = await prepare.prepare(force_detect=args.force_detect, force_assess=args.force_cards,
                                  force_deep=args.force_deep, assess=not args.no_cards, say=print)  # fmt: skip
    except prepare.PrepareError as exc:
        print(f"prepare: {exc}", file=sys.stderr)
        return 1
    finally:
        await sync.flush_calls()
        await snowflake_rest.client().aclose()
        await tiger.close_pool()
    summarize(p)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force-detect", action="store_true", help="re-run anomaly detection over the night")
    parser.add_argument("--force-cards", action="store_true", help="regenerate cached Cortex cards")
    parser.add_argument("--no-cards", action="store_true", help="skip the Cortex cards")
    parser.add_argument("--force-deep", action="store_true", help="redo cached note extractions and forecasts")
    args = parser.parse_args()
    if not get_settings().tiger_configured:
        print("prepare: TIGER_DATABASE_URL is not set in .env", file=sys.stderr)
        return 1
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
