"""Destructive resets live here (AGENTS.md rule 8).

`truncate_history()` is what `make seed` uses to clear Tiger before regenerating synthetic history;
`truncate_snowflake()` empties the VIGIL.CORE copies before the seed's bulk load (`make sf-load` too).
The anomaly models in VIGIL.ML are kept; `make train` replaces them.

Targeted commands (run from backend/):

* `uv run python scripts/reset_demo.py rescript --resident 42`: after editing one cast member's script in
  `app/sim/cast.py`, regenerate that resident's history with the seed's RNG and replace only their rows from noon
  before last night to the end of the seed (vitals, events; caggs refreshed; the Snowflake copies of VITALS_15M,
  EVENTS, CHECKS, NURSE_NOTES replaced; their cached forecasts, cards and note extractions for the night removed).
  Every other resident and the anomaly models are untouched: last night is held out of training and baselines,
  so no `make train` is needed. Then run Prepare replay with "re-run anomaly detection".
  Refuses if the regenerated history before the window doesn't match Tiger exactly (a changed generator).
* `uv run python scripts/reset_demo.py remove-notes --resident 23 50 --since 2026-10-18T19:55Z`: remove test
  notes typed or spoken into live residents, with everything they caused since `--since`: the non-routine notes,
  alerts, priority snapshots, Snowflake note extractions / forecasts / AI cards (Tiger and Snowflake) and the
  recorded audio. Restart the backend afterwards so the evaluator and the card cache start from the clean state.
* `uv run python scripts/reset_demo.py clear-forecasts`: remove last night's cached forecasts (e.g. after changing
  FORECAST_TRAIN_HOURS), so the next Prepare replay remakes exactly the ones its plan asks for.
"""

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta

import numpy as np

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger

HISTORY_TABLES = (
    "vitals",
    "events",
    "priority_snapshots",
    "replay_snapshots",
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


# --------------------------------------------------------------------------- rescript one resident's night


def _sf_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def regenerate(seed_meta: dict, night: date, rid: int, tz) -> tuple[dict[str, np.ndarray], list]:
    """The resident's whole seeded history, generated exactly as `make seed` did (same seed, day by day)."""
    from app.sim import cast
    from app.sim import facility as fac

    settings = get_settings()
    t0 = int(datetime.fromisoformat(seed_meta["start"]).timestamp())
    t_end = int(datetime.fromisoformat(seed_meta["end"]).timestamp())
    roster = [r for r in cast.build_roster(seed_meta["seed"]) if r.resident_id == rid]
    sim = cast.build_sims(seed_meta["seed"], tz, roster, night, settings.r120_resting_hr)[rid]
    n, per = (t_end - t0) // fac.TICK_S, 86400 // fac.TICK_S
    parts: dict[str, list] = {}
    events: list = []
    for off in range(0, n, per):
        batch = sim.step(t0 + off * fac.TICK_S, min(per, n - off))
        for k, v in batch.cols.items():
            parts.setdefault(k, []).append(v)
        events += batch.events
    return {k: np.concatenate(v) for k, v in parts.items()}, events


async def rescript(rid: int) -> None:
    from app.deep import sync
    from app.sim import cast
    from app.sim import facility as fac
    from app.sim.replay import PREPARED_KEY

    settings = get_settings()
    tz = fac.tzinfo(settings.facility_tz)
    async with tiger.connect(application_name="vigil-reset") as conn:
        seed_meta = await tiger.get_meta(conn, "seed")
        night = date.fromisoformat((await tiger.get_meta(conn, "scripted_night"))["date"])
        window = cast.scripted_window(night, tz)
        lo = datetime.fromtimestamp(window["holdout_start"], UTC)
        hi = min(datetime.fromtimestamp(window["holdout_end"], UTC), datetime.fromisoformat(seed_meta["end"]))
        print(f"rescript R-{rid}: night of {night}, replacing {lo:%Y-%m-%d %H:%M} → {hi:%Y-%m-%d %H:%M} UTC")
        cols, events = await asyncio.to_thread(regenerate, seed_meta, night, rid, tz)

        # Safety: the 6 h before the window must match Tiger exactly (same generator, same seed).
        check_lo = lo - timedelta(hours=6)
        db = await tiger.fetch_dicts(
            conn, "SELECT ts, hr FROM vitals WHERE resident_id = %s AND ts >= %s AND ts < %s ORDER BY ts",
            (rid, check_lo, lo),
        )  # fmt: skip
        us = cols["ts"]
        m = (us >= check_lo.timestamp() * 1e6) & (us < lo.timestamp() * 1e6)
        gen_hr = cols["hr"][m].astype(np.float32)
        db_hr = np.array([np.nan if r["hr"] is None else r["hr"] for r in db], dtype=np.float32)
        if len(db) != int(m.sum()) or not np.allclose(gen_hr, db_hr, atol=1e-3, equal_nan=True):
            raise SystemExit(f"rescript: regenerated history before the window doesn't match Tiger ({len(db)} vs "
                             f"{int(m.sum())} rows): the generator changed; use `make seed` instead")  # fmt: skip
        print(f"  check        6 h before the window match Tiger exactly ({len(db):,} rows)")

        win = (us >= lo.timestamp() * 1e6) & (us < hi.timestamp() * 1e6)
        new_cols = {k: v[win] for k, v in cols.items()}
        new_events = [e for e in events if lo.timestamp() <= e.ts < hi.timestamp()]
        async with conn.transaction():
            await tiger.execute(
                conn, "DELETE FROM vitals WHERE resident_id = %s AND ts >= %s AND ts < %s", (rid, lo, hi)
            )
            await tiger.copy_binary(conn, "vitals", tiger.VITALS_COLUMNS,
                                    tiger.encode_copy_binary(tiger.VITALS_COLUMNS, new_cols))  # fmt: skip
            await tiger.execute(
                conn, "DELETE FROM events WHERE resident_id = %s AND ts >= %s AND ts < %s", (rid, lo, hi)
            )
            await tiger.write_events(conn, [(e.ts, e.resident_id, e.type, e.staff_id, e.payload) for e in new_events])
            await tiger.execute(
                conn,
                """DELETE FROM ai_insights WHERE resident_id = %s AND as_of >= %s AND as_of < %s
                   AND kind IN ('forecast', 'assessment', 'note_extract')""",
                (rid, lo, hi + timedelta(hours=2)),
            )
            await tiger.execute(conn, "DELETE FROM replay_snapshots WHERE resident_id = %s AND ts >= %s AND ts < %s",
                                (rid, lo, hi))  # fmt: skip
            # The prepared replay no longer matches this night: replay asks for Prepare again (as during a prepare).
            await tiger.set_meta(conn, PREPARED_KEY, {"in_progress": True, "stale": f"rescript R-{rid}"})
        print(f"  tiger        {int(win.sum()):,} vitals rows, {len(new_events)} events replaced; cached forecasts, "
              "cards and note extractions for the night removed")  # fmt: skip
        await tiger.refresh_caggs(conn, lo - timedelta(hours=1), hi + timedelta(hours=1))
        print("  caggs        vitals_1m → vitals_15m → vitals_1h refreshed over the window")

        if not settings.snowflake_configured:
            print("  snowflake    not configured: skipped")
            return
        if not await tiger.try_lock_sync(conn):
            print("  snowflake    waiting for the backend's sync cycle …")
            await tiger.lock_sync(conn)
        try:
            vit = [r for r in await tiger.vitals_15m_closed(conn, lo - timedelta(minutes=15), hi)
                   if r["resident_id"] == rid]  # fmt: skip
            evs = [r for r in await tiger.events_between(conn, lo - timedelta(microseconds=1), hi)
                   if r["resident_id"] == rid and lo <= r["ts"] < hi]  # fmt: skip
            synced_at = datetime.now(UTC)

            def snowflake() -> dict[str, int]:
                sconn = sf.connect()
                try:
                    a, b = _sf_ts(lo), _sf_ts(hi)
                    sf.query(sconn, "DELETE FROM CORE.VITALS_15M WHERE RESIDENT_ID = %s AND BUCKET_TS >= %s AND BUCKET_TS < %s",
                             (rid, a, b), feature="reset")  # fmt: skip
                    for table in ("EVENTS", "CHECKS", "NURSE_NOTES", "ALERTS", "NOTE_EXTRACTS"):
                        col = "NOTE_TS" if table == "NOTE_EXTRACTS" else "TS"
                        sf.query(sconn, f"DELETE FROM CORE.{table} WHERE RESIDENT_ID = %s AND {col} >= %s AND {col} < %s",
                                 (rid, a, b), feature="reset")  # fmt: skip
                    for table in ("FORECAST_RESULTS", "AI_ASSESSMENTS"):
                        sf.query(sconn, f"DELETE FROM CORE.{table} WHERE RESIDENT_ID = %s AND AS_OF >= %s AND AS_OF < %s",
                                 (rid, a, _sf_ts(hi + timedelta(hours=2))), feature="reset")  # fmt: skip
                    out = {"VITALS_15M": sf.write_df(sconn, sync.vitals_frame(vit, synced_at), "VITALS_15M",
                                                     feature="reset")}  # fmt: skip
                    out |= sync._write_events(sconn, evs, synced_at)
                    return out
                finally:
                    sconn.close()

            counts = await asyncio.to_thread(snowflake)
            print("  snowflake    replaced " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        finally:
            await tiger.unlock_sync(conn)
    print("rescript: done. Next: Prepare replay with 're-run anomaly detection' (make prepare -- --force-detect).")


# --------------------------------------------------------------------------- remove test notes from live residents


async def remove_notes(rids: list[int], since: datetime) -> None:
    async with tiger.connect(application_name="vigil-reset") as conn:
        notes = await tiger.fetch_dicts(
            conn,
            """SELECT ts, resident_id, payload->>'source' AS source, left(payload->>'text', 60) AS text FROM events
               WHERE resident_id = ANY(%s) AND ts >= %s AND type = 'note_added'
                 AND NOT coalesce((payload->>'routine')::bool, false) ORDER BY ts""",
            (rids, since),
        )
        for n in notes:
            print(f"  note         R-{n['resident_id']} {n['ts']:%Y-%m-%d %H:%M:%S}Z {n['source']}: {n['text']}")
        async with conn.transaction():
            await tiger.execute(
                conn,
                """DELETE FROM events WHERE resident_id = ANY(%s) AND ts >= %s
                   AND (type IN ('alert_raised', 'alert_acknowledged', 'alert_resolved')
                        OR (type = 'note_added' AND NOT coalesce((payload->>'routine')::bool, false)))""",
                (rids, since),
            )
            await tiger.execute(conn, "DELETE FROM ai_insights WHERE resident_id = ANY(%s) AND as_of >= %s "
                                      "AND kind IN ('note_extract', 'forecast', 'assessment')", (rids, since))  # fmt: skip
            await tiger.execute(conn, "DELETE FROM priority_snapshots WHERE resident_id = ANY(%s) AND ts >= %s",
                                (rids, since))  # fmt: skip
        print(
            f"  tiger        removed {len(notes)} note(s) and the alerts, snapshots and Snowflake results they caused"
        )
        if not get_settings().snowflake_configured:
            return

        def snowflake() -> None:
            sconn = sf.connect()
            try:
                marks = ", ".join(["%s"] * len(rids))
                ts = _sf_ts(since)
                for table, col in (("NURSE_NOTES", "TS"), ("ALERTS", "TS"), ("NOTE_EXTRACTS", "NOTE_TS"),
                                   ("FORECAST_RESULTS", "AS_OF"), ("AI_ASSESSMENTS", "AS_OF")):  # fmt: skip
                    extra = " AND NOT ROUTINE" if table == "NURSE_NOTES" else ""
                    sf.query(sconn, f"DELETE FROM CORE.{table} WHERE RESIDENT_ID IN ({marks}) AND {col} >= %s{extra}",
                             (*rids, ts), feature="reset")  # fmt: skip
                sf.query(sconn, f"""DELETE FROM CORE.EVENTS WHERE RESIDENT_ID IN ({marks}) AND TS >= %s
                                    AND (TYPE LIKE 'alert_%%' OR (TYPE = 'note_added'
                                         AND NOT COALESCE(PAYLOAD:routine::BOOLEAN, FALSE)))""",
                         (*rids, ts), feature="reset")  # fmt: skip
                for rid in rids:
                    sf.query(sconn, f"REMOVE @CORE.NOTE_AUDIO/r{rid:03d}/", feature="reset")
            finally:
                sconn.close()

        await asyncio.to_thread(snowflake)
        print("  snowflake    removed the same rows from CORE (notes, alerts, extractions, forecasts, cards) + audio")


# --------------------------------------------------------------------------- clear last night's forecast cache


async def clear_night_forecasts() -> None:
    """Remove every cached forecast for last night (Tiger ai_insights + Snowflake FORECAST_RESULTS), e.g. after
    changing FORECAST_TRAIN_HOURS, so Prepare replay remakes exactly the forecasts its plan asks for."""
    from app.sim.replay import load_night

    async with tiger.connect(application_name="vigil-reset") as conn:
        night = await load_night(conn)
        if night is None:
            raise SystemExit("clear-forecasts: no recorded night (run `make seed`)")
        lo, hi = night.anchor - timedelta(hours=6), night.end + timedelta(hours=1)
        (n,) = await tiger.fetchone(
            conn, "SELECT count(*) FROM ai_insights WHERE kind = 'forecast' AND as_of >= %s AND as_of < %s", (lo, hi)
        )
        await tiger.execute(conn, "DELETE FROM ai_insights WHERE kind = 'forecast' AND as_of >= %s AND as_of < %s",
                            (lo, hi))  # fmt: skip
        print(f"  tiger        removed {n} cached forecasts ({lo:%H:%M} → {hi:%H:%M} UTC)")
        if get_settings().snowflake_configured:

            def snowflake() -> None:
                sconn = sf.connect()
                try:
                    sf.query(sconn, "DELETE FROM CORE.FORECAST_RESULTS WHERE AS_OF >= %s AND AS_OF < %s",
                             (_sf_ts(lo), _sf_ts(hi)), feature="reset")  # fmt: skip
                finally:
                    sconn.close()

            await asyncio.to_thread(snowflake)
            print("  snowflake    removed the same from CORE.FORECAST_RESULTS")


def _utc(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("rescript", help="regenerate one resident's scripted night after editing app/sim/cast.py")
    p.add_argument("--resident", type=int, required=True)
    p = sub.add_parser("remove-notes", help="remove test notes (and what they caused) from live residents")
    p.add_argument("--resident", type=int, nargs="+", required=True)
    p.add_argument("--since", type=_utc, required=True, help="ISO time, UTC unless it has an offset")
    sub.add_parser("clear-forecasts", help="remove last night's cached forecasts before re-preparing")
    args = parser.parse_args()
    if args.cmd == "rescript":
        asyncio.run(rescript(args.resident))
    elif args.cmd == "remove-notes":
        asyncio.run(remove_notes(args.resident, args.since))
    elif args.cmd == "clear-forecasts":
        asyncio.run(clear_night_forecasts())
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
