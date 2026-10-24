"""Live simulator: backfill from the last stored row to now, then stream every simulated resident every 5 s.

R-120 (the presenter) normally streams from the Polar band and the iPhone, so the loop simulates him
only while the "simulated band" fallback (`sim_state.simulated_band`) is on. Rows are written only for
ticks that have already happened: the loop never writes a future timestamp. The scripted night is read
from `sim_meta`, so a backend started right after `make seed` continues the cast's story seamlessly.
"""

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

import psycopg

from app.config import Settings, get_settings
from app.db import tiger
from app.sim import cast
from app.sim import facility as fac
from app.sim.injectors import Injected, InjectError
from app.sim.physiology import ResidentSim, SimBatch
from app.state import sim_state

log = logging.getLogger(__name__)

APP_NAME = "vigil-live"
CATCH_UP_TICKS = 180  # 15 minutes of ticks per COPY while catching up
REFRESH_AFTER_GAP_S = 2 * 3600  # longer backfills refresh the continuous aggregates themselves
LOG_EVERY_S = 60


def current_tick(now: float | None = None) -> int:
    """Latest tick boundary that is not in the future."""
    now = time.time() if now is None else now
    return int(now // fac.TICK_S) * fac.TICK_S


@dataclass
class LiveStats:
    phase: str = "starting"  # starting | backfill | streaming | paused | paused: seeding | standby | error | disabled
    residents: int = 0
    last_tick: str | None = None
    rows_written: int = 0
    events_written: int = 0
    backfilled_rows: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LiveLoop:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.tz = fac.tzinfo(self.settings.facility_tz)
        self.seed = self.settings.sim_seed
        self.roster = cast.build_roster(self.seed)
        self.sims: dict[int, ResidentSim] = {}
        self.night: date | None = None
        self.cursor = 0  # next tick (epoch s) to write
        self.stats = LiveStats()
        self._window_rows = 0
        self._window_start = time.monotonic()
        self._seed_seen = False

    # ------------------------------------------------------------------ setup

    def _build(self, seed: int, night: date | None) -> None:
        """Simulate with the seed and scripted night the stored history was generated with."""
        if self.sims and (seed, night) == (self.seed, self.night):
            return
        if seed != self.seed:
            self.roster = cast.build_roster(seed)
        self.seed, self.night = seed, night
        self.sims = cast.build_sims(seed, self.tz, self.roster, night, self.settings.r120_resting_hr)

    def active_ids(self) -> list[int]:
        return [rid for rid in self.sims if rid != fac.LIVE_RESIDENT_ID or sim_state.simulated_band]

    def inject(self, scenario: Injected) -> None:
        """Add an injected scenario to a live resident (dropping their expired injections)."""
        sim = self.sims.get(scenario.resident_id)
        if sim is None:
            raise InjectError(404, f"no simulated resident {scenario.resident_id}")
        if scenario.resident_id not in self.active_ids():
            raise InjectError(
                409, f"R-{scenario.resident_id} streams from real devices; turn on the simulated band first"
            )
        now = time.time()
        kept = [s for s in sim.scenarios if not (isinstance(s, Injected) and s.end < now)]
        sim.scenarios = [*kept, scenario]  # rebind, so a step running in the worker thread keeps its list
        log.info("live: injected %s for R-%d until %s", scenario.kind, scenario.resident_id, fac.utc_iso(scenario.end))

    def clear_injections(self) -> int:
        """End every injected scenario now (admin reset); scripted cast scenarios stay."""
        n = 0
        for sim in self.sims.values():
            kept = [s for s in sim.scenarios if not isinstance(s, Injected)]
            n += len(sim.scenarios) - len(kept)
            sim.scenarios = kept
        if n:
            log.info("live: cleared %d injected scenario(s)", n)
        return n

    def injections(self) -> list[dict]:
        now = time.time()
        return [
            s.describe()
            for sim in self.sims.values()
            for s in sim.scenarios
            if isinstance(s, Injected) and s.end >= now
        ]

    async def _prepare(self, conn: psycopg.AsyncConnection) -> None:
        seeded = await tiger.get_meta(conn, "seed") or {}
        night = (await tiger.get_meta(conn, "scripted_night") or {}).get("date")
        self._build(int(seeded.get("seed", self.seed)), date.fromisoformat(night) if night else None)

        cap_h = self.settings.sim_backfill_max_hours
        (last,) = await tiger.fetchone(
            conn,
            "SELECT max(ts) FROM vitals WHERE source = 0 AND ts > now() - make_interval(secs => %s)",
            (cap_h * 3600,),
        )
        now_tick = current_tick()
        if last is None:
            self.cursor = now_tick
            log.warning("live: no simulated rows in the last %.0f h — streaming from now (run `make seed`)", cap_h)
        else:
            self.cursor = current_tick(last.timestamp()) + fac.TICK_S
            gap_min = (now_tick - self.cursor) / 60
            log.info("live: last simulated row %s, backfilling %.1f min", last.isoformat(), max(gap_min, 0))
        for sim in self.sims.values():  # the cursor may have moved back: don't diff bed state against the future
            sim.state.last_in_bed = None

    # ------------------------------------------------------------------ main loop

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with tiger.connect(application_name=APP_NAME) as conn:
                    if not await tiger.try_lock_live_writer(conn):
                        self.stats.phase = "standby"  # another backend is already streaming into this Tiger
                        log.warning("live: another live loop holds the writer lease — standing by")
                        await asyncio.sleep(30)
                        continue
                    await self._prepare(conn)
                    backoff = 1.0
                    await self._stream(conn)
            except asyncio.CancelledError:
                raise
            except tiger.TigerNotConfigured:
                self.stats.phase = "disabled"
                log.warning("live: TIGER_DATABASE_URL not set — live simulator disabled")
                return
            except Exception as exc:  # keep going: reconnect and backfill whatever was missed
                if tiger.cancelling():  # psycopg can turn a cancel mid-COPY into QueryCanceled: still a cancel
                    raise asyncio.CancelledError from exc
                self.stats.phase = "error"
                self.stats.last_error = " ".join(str(exc).split())[:200]
                log.warning("live: %s — retrying in %.0f s", self.stats.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _stream(self, conn: psycopg.AsyncConnection) -> None:
        gap_start = self.cursor
        while True:
            if sim_state.paused:
                self.stats.phase = "paused"
                self.cursor = current_tick() + fac.TICK_S  # resume from "now": the pause stays a gap
                gap_start = self.cursor
                await asyncio.sleep(0.5)
                continue

            now_tick = current_tick()
            if self.cursor <= now_tick:
                if not await tiger.try_lock_shared_for_write(conn):  # `make seed` is rewriting history
                    self.stats.phase = "paused: seeding"
                    self._seed_seen = True
                    await asyncio.sleep(2)
                    continue
                try:
                    if self._seed_seen:  # history was replaced: start again from its new end
                        self._seed_seen = False
                        await self._prepare(conn)
                        gap_start = self.cursor
                        continue
                    n = min(CATCH_UP_TICKS, (now_tick - self.cursor) // fac.TICK_S + 1)
                    self.stats.phase = "backfill" if n > 2 else "streaming"
                    rows = await self._write(conn, self.cursor, n)
                    if n > 2:
                        self.stats.backfilled_rows += rows
                    self.cursor += n * fac.TICK_S
                finally:
                    await tiger.unlock_shared_for_write(conn)
                continue  # keep going until caught up

            if self.cursor - gap_start > REFRESH_AFTER_GAP_S:
                start, end = (datetime.fromtimestamp(t, UTC) for t in (gap_start, self.cursor))
                log.info("live: backfilled %s → %s, refreshing continuous aggregates", start, end)
                await tiger.refresh_caggs(conn, start, end)
            gap_start = self.cursor
            self._maybe_log()
            await asyncio.sleep(max(0.05, self.cursor - time.time() + 0.05))

    async def _write(self, conn: psycopg.AsyncConnection, t0: int, n: int) -> int:
        ids = self.active_ids()
        batches = await asyncio.to_thread(self._generate, ids, t0, n)
        cols = tiger.concat_columns([b.cols for b in batches])
        events = sorted((e for b in batches for e in b.events), key=lambda e: e.ts)
        rows = await tiger.write_vitals(conn, cols)
        if events:
            self.stats.events_written += await tiger.write_events(conn, events)
        self.stats.rows_written += rows
        self.stats.residents = len(ids)
        self.stats.last_tick = fac.utc_iso(t0 + (n - 1) * fac.TICK_S)
        self._window_rows += rows
        return rows

    def _generate(self, ids: list[int], t0: int, n: int) -> list[SimBatch]:
        return [self.sims[rid].step(t0, n) for rid in ids]

    def _maybe_log(self) -> None:
        elapsed = time.monotonic() - self._window_start
        if elapsed >= LOG_EVERY_S:
            log.info(
                "live: +%d rows in %.0f s (%d residents, last tick %s)",
                self._window_rows,
                elapsed,
                self.stats.residents,
                self.stats.last_tick,
            )
            self._window_rows = 0
            self._window_start = time.monotonic()


live: LiveLoop | None = None


def start() -> asyncio.Task | None:
    """Create the singleton loop and its task (called from the FastAPI lifespan)."""
    global live
    settings = get_settings()
    if not (settings.sim_live_enabled and settings.tiger_configured):
        return None
    live = LiveLoop(settings)
    return asyncio.create_task(live.run(), name="live-sim")
