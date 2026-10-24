"""Replay: last night's recorded data through the live pipeline (SPEC §4).

A cursor moves through the scripted night (22:00–06:30 facility time, `sim_meta.scripted_night`) at 30/60/120×.
At every STEP of replay time the real fast-tier evaluator scores all 120 residents `as_of` the cursor, through
its own AlertGate (hysteresis, persistence, suppression), and the queue goes out on /ws/live exactly like the
live one, with a `replay` message carrying the cursor. Nothing is written: the stored history is the record.

Snowflake is never called during a replay. Anomaly results are read from Tiger `ai_insights` like live (each
stamped with when live would have known it), and the AI cards are the ones `prepare` generated at bookmarks and
escalations (`app/sim/prepare.py`), shown only once the cursor has reached their `as_of`.

Steps lie on a fixed grid (night start − WARMUP, every STEP), the same grid the prepare pass scored, so a replay
from the start reproduces its tiers exactly. A seek snaps to the grid and seeds the gate from the
replay_snapshots that pass wrote, the way the live evaluator recovers its gate after a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.db import tiger
from app.deep.assess import MAX_AGE
from app.scoring import evaluator as ev
from app.scoring.evaluator import AlertGate, Queue, ScoringContext
from app.scoring.rules import NightClock
from app.sim import cast
from app.state import sim_state
from app.ws.hub import hub

log = logging.getLogger(__name__)

STEP = timedelta(minutes=2)  # replay time per evaluation: 30× → every 4 s, 60× → 2 s (the live cadence), 120× → 1 s
SPEEDS = (30, 60, 120)
DEFAULT_SPEED = 60
WARMUP = timedelta(hours=1)  # the prepare pass starts this long before 22:00 so the gate is warm at 22:00
SEED_LOOKBACK_S = 600  # a seek seeds the gate from snapshots this recent
PREPARED_KEY = "replay_prepared"


class ReplayError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- the night (pure)


def _ts(value: str | datetime) -> datetime:
    ts = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return ts.astimezone(UTC)


@dataclass(frozen=True)
class Night:
    date: str  # evening date of the scripted night
    start: datetime  # 22:00 facility time
    end: datetime  # 06:30
    bookmarks: tuple[dict, ...] = ()

    @classmethod
    def from_meta(cls, meta: dict) -> Night:
        marks = tuple(
            {"ts": _ts(b["ts"]).isoformat(), "resident_id": b["resident_id"], "label": b["label"]}
            for b in meta.get("bookmarks") or ()
        )
        return cls(meta["date"], _ts(meta["start"]), _ts(meta["end"]), marks)

    @property
    def anchor(self) -> datetime:
        """The first grid point: where the prepare pass starts scoring."""
        return self.start - WARMUP

    def snap(self, ts: datetime) -> datetime:
        """The grid point at or before ts, inside [start, end]."""
        ts = min(max(ts, self.start), self.end)
        return self.anchor + STEP * int((ts - self.anchor) / STEP)

    def at_local(self, hhmm: str, tz: ZoneInfo) -> datetime:
        """ "HH:MM" facility time on this night: evening times on its first date, times before noon the morning after."""
        try:
            h, m = (int(x) for x in hhmm.strip().split(":"))
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError
        except ValueError as exc:
            raise ReplayError(f"time must be HH:MM, got {hhmm!r}", 400) from exc
        day = date.fromisoformat(self.date) + timedelta(days=0 if h >= 12 else 1)
        return datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(UTC)

    def grid(self, start: datetime | None = None, end: datetime | None = None) -> list[datetime]:
        """Grid points from `start` (default: the anchor) through `end` (default: the night's end)."""
        t, end = start or self.anchor, end or self.end
        out = []
        while t <= end:
            out.append(t)
            t += STEP
        return out


async def load_night(conn) -> Night | None:
    """The seeded night, with bookmarks from the code (`cast.BOOKMARKS`) so retuning them needs no re-seed."""
    meta = await tiger.get_meta(conn, "scripted_night")
    if not meta:
        return None
    tz = ZoneInfo(get_settings().facility_tz)
    return Night.from_meta(meta | {"bookmarks": cast.bookmarks(date.fromisoformat(meta["date"]), tz)})


async def load_prepared(conn) -> dict | None:
    """What `prepare` stored for the current seed, or None (never prepared, or prepared before a re-seed)."""
    prepared = await tiger.get_meta(conn, PREPARED_KEY)
    seed = await tiger.get_meta(conn, "seed")
    if not prepared or not seed or prepared.get("seeded_at") != seed.get("seeded_at"):
        return None
    return prepared


def prepared_summary(prepared: dict | None) -> dict | None:
    if not prepared:
        return None
    keys = ("prepared_at", "seconds", "step_s", "detection", "gated", "assessments_summary", "forecasts")
    return {k: prepared.get(k) for k in keys}


def headline_at(cards: list[dict], at: datetime) -> dict | None:
    """{text, at} of the newest card (sorted by as_of) known by `at` and younger than the card age limit."""
    best = None
    for c in cards:
        made = _ts(c["as_of"])
        if made > at:
            break
        best = c if at - made <= MAX_AGE else None
    return None if best is None else {"text": best["headline"], "at": best["as_of"]}


# --------------------------------------------------------------------------- the engine


@dataclass
class ReplayStats:
    steps: int = 0
    last_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    behind: int = 0  # steps that took longer than their wall-clock slot (playback ran slower than the speed)
    last_error: str | None = None
    _total_ms: float = field(default=0.0, repr=False)

    def record(self, ms: float, slot_s: float) -> None:
        self.steps += 1
        self._total_ms += ms
        self.last_ms = round(ms, 1)
        self.avg_ms = round(self._total_ms / self.steps, 1)
        self.max_ms = round(max(self.max_ms, ms), 1)
        if ms / 1000 > slot_s:
            self.behind += 1

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


class ReplayEngine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.tz = ZoneInfo(self.settings.facility_tz)
        self.clock = NightClock(tz=self.tz)  # the real facility clock: no force-night, no display offset
        self.night: Night | None = None
        self.prepared: dict | None = None
        self.cursor: datetime | None = None
        self.speed = DEFAULT_SPEED
        self.playing = False
        self.gate = AlertGate()
        self.ctx: ScoringContext | None = None
        self.latest: Queue | None = None
        self.cards: dict[int, list[dict]] = {}  # rid → prepared Cortex cards, by as_of
        self.stats = ReplayStats()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._due = 0.0
        self._task: asyncio.Task | None = None

    # ---- state

    @property
    def active(self) -> bool:
        return sim_state.mode == "replay"

    @property
    def slot_s(self) -> float:
        return STEP.total_seconds() / self.speed

    def brief(self) -> dict | None:
        """The replay part of the sim state (heartbeats, `sim` messages): enough to run the header clock."""
        if self.night is None or self.cursor is None:
            return None
        return {
            "cursor": self.cursor.isoformat(),
            "speed": self.speed,
            "playing": self.playing,
            "step_s": int(STEP.total_seconds()),
            "start": self.night.start.isoformat(),
            "end": self.night.end.isoformat(),
        }

    def status(self) -> dict:
        n = self.night
        return {
            "active": self.active,
            "cursor": self.cursor.isoformat() if self.cursor else None,
            "speed": self.speed,
            "speeds": list(SPEEDS),
            "playing": self.playing,
            "step_s": int(STEP.total_seconds()),
            "at_end": bool(n and self.cursor and self.cursor + STEP > n.end),
            "night": None if n is None else {"date": n.date, "start": n.start.isoformat(), "end": n.end.isoformat()},
            "bookmarks": list(n.bookmarks) if n else [],
            "prepared": prepared_summary(self.prepared),
            "cards": sum(len(v) for v in self.cards.values()),
            "stats": self.stats.to_dict(),
        }

    def headline_for(self, rid: int, at: datetime) -> dict | None:
        return headline_at(self.cards.get(rid, []), at)

    def card(self, rid: int) -> dict | None:
        """This resident's full card in the replay's latest queue."""
        if self.latest is None:
            return None
        for rank, it in enumerate(self.latest.items, start=1):
            if it.profile.resident_id == rid:
                return it.to_dict(rank)
        return None

    # ---- commands (API)

    async def refresh_meta(self) -> tuple[Night | None, dict | None]:
        async with tiger.pooled() as conn:
            self.night = await load_night(conn)
            self.prepared = await load_prepared(conn)
            if self.night is not None and not self.active:
                self.cards = await self._load_cards(conn)  # so /admin shows how many cards are ready
        return self.night, self.prepared

    async def start(self, *, at: datetime | None = None, speed: int | None = None, play: bool = True) -> dict:
        """Enter replay mode (or resume it): load the night and the prepared cards, seek, play."""
        async with self._lock:
            if speed is not None:
                self._set_speed(speed)
            if not self.active:
                async with tiger.pooled() as conn:
                    night = await load_night(conn)
                    if night is None:
                        raise ReplayError("No recorded night to replay: run `make seed` first.")
                    prepared = await load_prepared(conn)
                    if prepared is None:
                        raise ReplayError(
                            "Run Prepare replay first: it precomputes the Snowflake results and AI cards."
                        )
                    self.night, self.prepared = night, prepared
                    self.ctx = await ev.load_context(conn)
                    self.cards = await self._load_cards(conn)
                sim_state.mode = "replay"
                try:
                    await self._seek(at or (self.cursor if self._resumable() else None) or self.night.start)
                except BaseException:
                    await self._back_to_live()  # never leave the app in a replay with nothing to show
                    raise
            elif at is not None:
                await self._seek(at)
            self.playing = play
            self._due = time.monotonic() + self.slot_s
            await self._publish_state()
        self._ensure_task()
        self._wake.set()
        return self.status()

    async def seek(self, at: datetime) -> dict:
        if not self.active:
            return await self.start(at=at, play=False)
        async with self._lock:
            await self._seek(at)
            self._due = time.monotonic() + self.slot_s
            await self._publish_state()
        self._wake.set()
        return self.status()

    async def pause(self) -> dict:
        async with self._lock:
            self.playing = False
            await self._publish_state()
        return self.status()

    async def set_speed(self, speed: int) -> dict:
        async with self._lock:
            self._set_speed(speed)
            self._due = time.monotonic() + self.slot_s
            await self._publish_state()
        self._wake.set()
        return self.status()

    async def stop(self) -> dict:
        """Back to live: the live evaluator's queue goes out again at once."""
        async with self._lock:
            await self._back_to_live()
        return self.status()

    async def _back_to_live(self) -> None:
        self.playing = False
        sim_state.mode = "live"
        sim_state.replay = None
        await hub.broadcast({"type": "sim", "ts": _now_iso(), "sim": sim_state.to_dict()})
        live = ev.evaluator
        if live is not None:
            if live.latest is not None:  # republish now: a paused simulator runs no cycle that would
                await hub.publish_queue(live.latest.to_wire())
            live.poke(republish=True)

    def _set_speed(self, speed: int) -> None:
        if speed not in SPEEDS:
            raise ReplayError(f"speed must be one of {', '.join(map(str, SPEEDS))}", 400)
        self.speed = speed

    def _resumable(self) -> bool:
        n = self.night
        return bool(n and self.cursor and n.start <= self.cursor <= n.end)

    async def reload_cards(self) -> None:
        """After a prepare: pick up the new cards without leaving the replay."""
        async with self._lock, tiger.pooled() as conn:
            self.cards = await self._load_cards(conn)

    async def _load_cards(self, conn) -> dict[int, list[dict]]:
        rows = await tiger.fetch_dicts(
            conn,
            """SELECT resident_id, as_of, payload FROM ai_insights
               WHERE kind = 'assessment' AND as_of > %s AND as_of <= %s AND payload->>'source' = 'cortex'
               ORDER BY resident_id, as_of""",
            (self.night.anchor - MAX_AGE, self.night.end),
        )
        cards: dict[int, list[dict]] = {}
        for r in rows:
            cards.setdefault(r["resident_id"], []).append(r["payload"] | {"as_of": r["as_of"].isoformat()})
        return cards

    # ---- evaluation

    async def _seek(self, at: datetime) -> None:
        """Snap to the grid, seed the gate from the prepare pass's snapshots, and score that moment."""
        ts = self.night.snap(at)
        self.gate = AlertGate()
        async with tiger.pooled() as conn:
            # The step before ts, so the seek's own evaluation updates the gate like any other step.
            prior = ts - timedelta(microseconds=1)
            for r in await tiger.latest_snapshots(conn, prior, SEED_LOOKBACK_S, table="replay_snapshots"):
                keys = (x.get("key", "") for x in r["factors"] or ())
                rules = [k.removeprefix("rule:") for k in keys if k.startswith("rule:")]
                self.gate.seed(r["resident_id"], r["tier"], r["score"], rules, r["ts"])
            check_scores = await ev.check_scores_at(conn, ts, table="replay_snapshots")
        await self._step(ts, alerts=False, seeked=True, check_scores=check_scores)

    async def _step(
        self, ts: datetime, *, alerts: bool = True, seeked: bool = False, check_scores: dict[int, float] | None = None
    ) -> Queue:
        t0 = time.perf_counter()
        async with tiger.pooled() as conn:
            if self.ctx is None or self.ctx.stale:
                self.ctx = await ev.load_context(conn)
            queue = await ev.evaluate(conn, ts, self.ctx, self.gate, self.clock, check_scores=check_scores)
        self.cursor, self.latest = ts, queue
        queue.total_ms = (time.perf_counter() - t0) * 1000
        await hub.publish_queue(queue.to_wire(self.clock, headline=self.headline_for))
        if alerts:
            for it in queue.items:
                if it.decision.alert:
                    await hub.broadcast(ev.alert_message(ts, it))
        sim_state.replay = self.brief()
        await hub.broadcast({"type": "replay", "ts": _now_iso(), "seeked": seeked, **(self.brief() or {})})
        self.stats.record(queue.total_ms, self.slot_s)
        return queue

    async def _publish_state(self) -> None:
        sim_state.replay = self.brief()
        await hub.broadcast({"type": "sim", "ts": _now_iso(), "sim": sim_state.to_dict()})
        await hub.broadcast({"type": "replay", "ts": _now_iso(), "seeked": False, **(self.brief() or {})})

    # ---- playback loop

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="replay")

    async def run(self) -> None:
        while True:
            self._wake.clear()
            if not (self.active and self.playing):
                await self._wake.wait()
                continue
            delay = self._due - time.monotonic()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), delay)
                continue
            started = time.monotonic()
            try:
                async with self._lock:
                    if not (self.active and self.playing) or self.cursor is None or self.night is None:
                        continue
                    if self.cursor + STEP > self.night.end:
                        self.playing = False  # the end of the night: stay on the last step
                        await self._publish_state()
                        continue
                    await self._step(self.cursor + STEP)
                    self._due = started + self.slot_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if tiger.cancelling():
                    raise asyncio.CancelledError from exc
                self.stats.last_error = " ".join(str(exc).split())[:200]
                log.warning("replay: step failed: %s", self.stats.last_error)
                self._due = time.monotonic() + 1.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


engine: ReplayEngine | None = None


def get_engine() -> ReplayEngine:
    global engine
    if engine is None:
        engine = ReplayEngine()
    return engine


def current_queue() -> Queue | None:
    """The queue the UI is showing: the replay's while replaying, else the live evaluator's."""
    if sim_state.mode == "replay":
        return engine.latest if engine is not None else None
    return ev.evaluator.latest if ev.evaluator is not None else None


def replay_cursor() -> datetime | None:
    """The replay cursor while replaying, else None (= live, now)."""
    if sim_state.mode == "replay" and engine is not None:
        return engine.cursor
    return None


def start() -> asyncio.Task | None:
    """The playback loop (idle until a replay starts); started with the app so shutdown cancels it."""
    if not get_settings().tiger_configured:
        return None
    eng = get_engine()
    eng._ensure_task()
    return eng._task
