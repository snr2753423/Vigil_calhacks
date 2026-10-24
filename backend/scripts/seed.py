"""`make seed DAYS=N`: truncate Tiger and regenerate N days of synthetic history ending now (SPEC §8).

Steps: migrations → pause Tiger jobs → truncate → residents/staff/devices → vitals via parallel binary
COPY → events → refresh the continuous aggregates → run the baseline job once → compress every
eligible chunk → resume jobs. Prints rows, timings and the compression ratio.

A running backend does not need to be stopped: its live loop pauses on an advisory lock while the seed
runs, then backfills from the end of the new history.
"""

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime

from psycopg.types.json import Jsonb
from reset_demo import truncate_history  # scripts/ is on sys.path when run as `python scripts/seed.py`

from app.config import get_settings
from app.db import tiger
from app.sim import cast
from app.sim import facility as fac
from app.sim.physiology import ResidentSim

TICKS_PER_DAY = 86400 // fac.TICK_S


def log(msg: str) -> None:
    print(msg, flush=True)


class Timer:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    def __str__(self) -> str:
        return f"{time.perf_counter() - self.start:.1f}s"


def human_bytes(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------- reference data


async def insert_reference(conn, roster: list[cast.Resident]) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO staff (staff_id, name, role, shift, unit) VALUES (%s, %s, %s, %s, %s)",
            [(s.staff_id, s.name, s.role, s.shift, s.unit) for s in fac.STAFF],
        )
        await cur.executemany(
            """INSERT INTO residents (resident_id, name, age, sex, room, unit, diagnoses, medications, flags, morse,
                                      mobility, cognition, code_status, emergency_contact, profile, is_live)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [
                (r.resident_id, r.name, r.age, r.sex, r.room, r.unit, list(r.diagnoses),
                 Jsonb([m.to_dict() for m in r.medications]), list(r.flags), r.morse, r.mobility, r.cognition,
                 r.code_status, Jsonb(r.emergency_contact), Jsonb(r.profile), r.is_live)
                for r in roster
            ],
        )  # fmt: skip
        devices = []
        for r in roster:
            if r.is_live:
                devices += [(f"POLAR-{r.resident_id}", r.resident_id, "wristband", "Polar Verity Sense", 1),
                            (f"PHONE-{r.resident_id}", r.resident_id, "bed_sensor", "iPhone (bed-sensor stand-in)", 2)]  # fmt: skip
            else:
                devices += [(f"WB-{r.resident_id:03d}", r.resident_id, "wristband", "Simulated wristband", 0),
                            (f"BED-{r.resident_id:03d}", r.resident_id, "bed_sensor", "Simulated bed sensor", 0)]  # fmt: skip
        await cur.executemany(
            "INSERT INTO devices (device_id, resident_id, kind, model, source, assigned_at) VALUES (%s, %s, %s, %s, %s, now())",
            devices,
        )


async def wait_for_jobs_idle(conn, job_ids: list[int], timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    announced = False
    while True:
        running = await tiger.fetchall(
            conn,
            "SELECT job_id FROM timescaledb_information.job_stats WHERE job_id = ANY(%s) AND job_status = 'Running'",
            (job_ids,),
        )
        if not running:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"Tiger jobs still running after {timeout_s:.0f}s: {[r[0] for r in running]}")
        if not announced:
            log(f"  jobs         waiting for running job(s) {[r[0] for r in running]} to finish …")
            announced = True
        await asyncio.sleep(2)


# --------------------------------------------------------------------------- vitals


async def load_vitals(sims: dict[int, ResidentSim], t0: int, n_ticks: int, writers: int) -> tuple[int, list]:
    """Generate every resident day by day and COPY the payloads over `writers` parallel connections."""
    jobs = [(rid, start) for rid in sims for start in range(0, n_ticks, TICKS_PER_DAY)]
    queue: asyncio.Queue = asyncio.Queue(maxsize=writers * 2)
    events: list = []
    written = 0
    total = len(sims) * n_ticks
    next_report = 0.1

    def generate(rid: int, offset: int) -> tuple[bytes, int, list]:
        batch = sims[rid].step(t0 + offset * fac.TICK_S, min(TICKS_PER_DAY, n_ticks - offset))
        return tiger.encode_copy_binary(tiger.VITALS_COLUMNS, batch.cols), batch.rows, batch.events

    async def producer() -> None:
        for rid, offset in jobs:  # per resident in time order, so AR noise and bed state carry over
            payload, rows, evs = await asyncio.to_thread(generate, rid, offset)
            events.extend(evs)
            await queue.put((payload, rows))
        for _ in range(writers):
            await queue.put(None)

    async def writer() -> None:
        nonlocal written, next_report
        async with tiger.connect(application_name="vigil-seed") as conn:
            while (item := await queue.get()) is not None:
                payload, rows = item
                await tiger.copy_binary(conn, "vitals", tiger.VITALS_COLUMNS, payload)
                written += rows
                if written / total >= next_report:
                    log(f"    {written / total:4.0%}  {written:>11,} rows")
                    next_report += 0.1

    await asyncio.gather(producer(), *(writer() for _ in range(writers)))
    return written, events


# --------------------------------------------------------------------------- main


async def run(days: int, seed: int, writers: int) -> None:
    settings = get_settings()
    tz = fac.tzinfo(settings.facility_tz)
    roster = cast.build_roster(seed)
    log(f"seed: {days} day(s) × {len(roster)} residents, seed={seed}, writers={writers}")
    total = Timer()

    async with tiger.connect(application_name="vigil-seed") as conn:
        t = Timer()
        await tiger.apply_migrations(conn)
        log(f"  migrations   ok ({t})")
        # A running backend's live loop pauses while we hold this lock, then resumes from the new history.
        await tiger.lock_for_seed(conn)

        now = datetime.now(UTC)
        t_end = (int(now.timestamp()) // fac.TICK_S) * fac.TICK_S
        t0 = t_end - days * 86400
        n_ticks = (t_end - t0) // fac.TICK_S
        night = fac.scripted_night_date(now, tz)
        window = cast.scripted_window(night, tz)
        if window["holdout_start"] - 2 * 3600 < t0:
            raise SystemExit("seed: DAYS must cover last night and the afternoon before it — use DAYS ≥ 2")
        sims = cast.build_sims(seed, tz, roster, night, settings.r120_resting_hr)
        start_dt, end_dt = datetime.fromtimestamp(t0, UTC), datetime.fromtimestamp(t_end, UTC)
        log(f"  range        {start_dt:%Y-%m-%d %H:%M} → {end_dt:%Y-%m-%d %H:%M} UTC")
        log(f"  last night   {night} 22:00 → 06:30 {settings.facility_tz} (scripted cast)")

        jobs = [
            r[0]
            for r in await tiger.fetchall(
                conn,
                # Cagg policies are listed under the cagg's view name (vitals_1m …).
                """SELECT job_id FROM timescaledb_information.jobs
                   WHERE hypertable_name IN ('vitals', 'priority_snapshots', 'vitals_1m', 'vitals_15m', 'vitals_1h')
                      OR proc_name = 'job_refresh_resident_baselines'""",
            )
        ]
        for job_id in jobs:
            await tiger.execute(conn, "SELECT alter_job(%s, scheduled => false)", (job_id,))
        try:
            await wait_for_jobs_idle(conn, jobs)  # pausing does not stop a run already in progress
            t = Timer()
            await truncate_history(conn)
            await insert_reference(conn, roster)
            await tiger.set_meta(conn, "facility", {"tz": settings.facility_tz, "name": fac.FACILITY_NAME}, merge=True)
            await tiger.set_meta(
                conn,
                "scripted_night",
                {
                    "date": night.isoformat(),
                    "start": fac.utc_iso(window["night_start"]),
                    "end": fac.utc_iso(window["night_end"]),
                    "bookmarks": cast.bookmarks(night, tz),
                },
            )
            await tiger.set_meta(
                conn,
                "baseline_holdout",
                {
                    "start": fac.utc_iso(window["holdout_start"]),
                    "end": fac.utc_iso(window["holdout_end"]),
                    "reason": "scripted last night is held out of baselines so replay is scored out-of-sample",
                },
            )
            log(f"  reset        truncated + {len(roster)} residents, {len(fac.STAFF)} staff, devices ({t})")

            t = Timer()
            log("  vitals       generating + binary COPY …")
            n_vitals, events = await load_vitals(sims, t0, n_ticks, writers)
            log(
                f"  vitals       {n_vitals:,} rows ({t}, {n_vitals / max(time.perf_counter() - t.start, 1e-9):,.0f} rows/s)"
            )

            t = Timer()
            events.sort(key=lambda e: e.ts)
            n_events = await tiger.write_events(conn, events)
            log(f"  events       {n_events:,} rows ({t})")

            await tiger.set_meta(
                conn,
                "seed",
                {"seed": seed, "days": days, "start": fac.utc_iso(t0), "end": fac.utc_iso(t_end),
                 "vitals_rows": n_vitals, "event_rows": n_events, "seeded_at": datetime.now(UTC).isoformat()},
            )  # fmt: skip

            t = Timer()
            await tiger.refresh_caggs(conn, None, None)
            counts = [
                (await tiger.fetchone(conn, f"SELECT count(*) FROM {c}"))[0]  # noqa: S608 (constant names)
                for c in tiger.CAGGS
            ]
            log("  caggs        refreshed " + ", ".join(f"{c} {n:,}" for c, n in zip(tiger.CAGGS, counts, strict=True))
                + f" ({t})")  # fmt: skip

            t = Timer()
            (job_id,) = await tiger.fetchone(
                conn,
                "SELECT job_id FROM timescaledb_information.jobs WHERE proc_name = 'job_refresh_resident_baselines'",
            )
            await tiger.execute(conn, "CALL run_job(%s)", (job_id,))
            rows, residents = await tiger.fetchone(
                conn, "SELECT count(*), count(DISTINCT resident_id) FROM resident_baselines"
            )
            log(f"  baselines    job {job_id}: {rows:,} rows for {residents} residents ({t})")

            t = Timer()
            (compressed,) = await tiger.fetchone(
                conn,
                """SELECT count(compress_chunk(c, if_not_compressed => true))
                   FROM show_chunks('vitals', older_than => INTERVAL '1 day') AS c""",
            )
            stats = await tiger.fetchone(
                conn,
                """SELECT total_chunks, number_compressed_chunks, before_compression_total_bytes,
                          after_compression_total_bytes, hypertable_size('vitals')
                   FROM hypertable_columnstore_stats('vitals')""",
            )
            chunks, n_comp, before, after, size = stats
            ratio = (before / after) if after else 0
            log(f"  compression  {compressed} chunk(s) compressed now; {n_comp}/{chunks} chunks in columnstore ({t})")
            if after:
                log(f"               {human_bytes(before)} → {human_bytes(after)}  = {ratio:.1f}× smaller; "
                    f"vitals total {human_bytes(size)}")  # fmt: skip
            await tiger.set_meta(
                conn,
                "seed_stats",
                {"compression_ratio": round(ratio, 2), "before_bytes": before, "after_bytes": after,
                 "chunks": chunks, "compressed_chunks": n_comp},
            )  # fmt: skip
        finally:
            for job_id in jobs:
                await tiger.execute(conn, "SELECT alter_job(%s, scheduled => true)", (job_id,))
            await tiger.unlock_for_seed(conn)

    log(f"seed: done in {total} — {n_vitals:,} vitals rows, {n_events:,} events")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=settings.seed_days)
    parser.add_argument("--seed", type=int, default=settings.sim_seed)
    parser.add_argument("--writers", type=int, default=4, help="parallel COPY connections")
    args = parser.parse_args()
    if not settings.tiger_configured:
        print("seed: TIGER_DATABASE_URL is not set in .env", file=sys.stderr)
        return 1
    asyncio.run(run(args.days, args.seed, args.writers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
