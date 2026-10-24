"""WS /ws/ingest: Polar HR + iPhone bed summaries, batch-written every 1 s (SPEC §10). Phase 7.

Device → server messages (JSON, one per frame):

* `{"type": "hello", "device": "polar" | "phone", "resident_id": 120}` → `hello` reply with the bed state
* Polar band (via Web Bluetooth on the Mac): `{resident_id, ts, hr, worn}` → a vitals row, source = 1
* iPhone bed sensor: `{resident_id, ts?, motion_energy, peak_jolt, gravity?: [x, y, z]}` → the bed state
  machine (`bed_state.BedTracker`) → a vitals row with `in_bed` / `bed_motion`, source = 2, plus a
  `bed_exit` / `bed_return` event on a transition. Replies `{type: "bed", ...}` so the phone shows the state.
* Calibration: `{"type": "calibrate", "action": "begin", "phase": "prep" | "empty" | "still" | "shift"}`, then
  `{"action": "finish"}` (or `"cancel"`) → `{type: "calibration", ...}` with the derived thresholds.

Rows are buffered and written to Tiger every second (at once after a bed transition) in one pipelined
transaction on the writer's own connection, never with a future
timestamp: a device clock ahead of the server, or more than 10 s behind, is replaced by the server's time.
A transition pokes the evaluator so the fall-prevention rule is scored within the same second, and is
broadcast on /ws/live. Only the live resident (R-120) takes device data, and none while the admin
"simulated band" fallback is simulating him.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.db import tiger
from app.devices.bed_state import PHASES, PREP, BedTracker, Calibration, Reading, Summary, Transition
from app.sim import facility as fac
from app.state import sim_state
from app.ws.hub import hub, utc_now_iso

log = logging.getLogger(__name__)

SOURCE_POLAR, SOURCE_PHONE = 1, 2
LIVE_RESIDENTS = frozenset({fac.LIVE_RESIDENT_ID})
APP_NAME = "vigil-ingest"
FLUSH_INTERVAL_S = 1.0
MAX_CLOCK_SKEW_S = 10.0
MAX_PENDING_ROWS = 3600  # rows kept for retry while Tiger is unreachable (~30 min of both devices)
CALIBRATION_META = "bed_calibration:{rid}"
STATE_LOOKBACK_H = 12
RESTORE_TIMEOUT_S = 5.0
RESTORE_RETRY_S = 30.0  # after a failed restore, devices run on defaults this long before trying again


def calibration_key(rid: int) -> str:
    return CALIBRATION_META.format(rid=rid)


def device_ts(value: Any, now: float) -> float:
    """The device's timestamp (ISO string or epoch ms) if it is plausible, else the server's `now`."""
    ts: float | None = None
    try:
        if isinstance(value, int | float) and math.isfinite(value):
            ts = float(value) / 1000.0
        elif isinstance(value, str) and value:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        ts = None
    if ts is None or ts > now or now - ts > MAX_CLOCK_SKEW_S:
        return now
    return ts


def _num(msg: dict, key: str, lo: float, hi: float) -> float | None:
    v = msg.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{key} must be a number in [{lo}, {hi}]")
    return float(v)


def _gravity(msg: dict) -> tuple[float, float, float] | None:
    g = msg.get("gravity")
    if g is None:
        return None
    if not (isinstance(g, list | tuple) and len(g) == 3 and all(isinstance(x, int | float) for x in g)):
        raise ValueError("gravity must be [x, y, z]")
    if not all(math.isfinite(x) and abs(x) < 50 for x in g):
        raise ValueError("gravity out of range")
    return float(g[0]), float(g[1]), float(g[2])


@dataclass
class DeviceStatus:
    kind: str  # polar | phone
    resident_id: int
    sockets: int = 0
    last_seen: float | None = None
    readings: int = 0
    last: dict = field(default_factory=dict)

    def to_dict(self, now: float) -> dict:
        age = None if self.last_seen is None else round(now - self.last_seen, 1)
        return asdict(self) | {
            "last_seen": None if self.last_seen is None else datetime.fromtimestamp(self.last_seen, UTC).isoformat(),
            "age_s": age,
            "online": self.sockets > 0 and age is not None and age < 10,
        }


@dataclass
class IngestStats:
    phase: str = "starting"  # starting | running | disabled | error
    rows_written: int = 0
    events_written: int = 0
    flushes: int = 0
    dropped_rows: int = 0
    ignored_simulated: int = 0  # readings for R-120 dropped while the simulated band was on
    last_flush_ms: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Ingest:
    def __init__(self) -> None:
        self.rows: list[tuple] = []  # (ts, rid, hr, worn, in_bed, bed_motion, source)
        self.events: list[tuple] = []  # (ts, rid, type, staff_id, payload)
        self.trackers: dict[int, BedTracker] = {}
        self.devices: dict[tuple[int, str], DeviceStatus] = {}
        self.stats = IngestStats()
        self._flush_now = asyncio.Event()
        self._restored: set[int] = set()
        self._restore_failed: dict[int, float] = {}  # rid → monotonic time of the last failed restore
        self._restore_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._background: set[asyncio.Task] = set()

    # ---- state

    def tracker(self, rid: int) -> BedTracker:
        return self.trackers.setdefault(rid, BedTracker())

    def device(self, rid: int, kind: str) -> DeviceStatus:
        return self.devices.setdefault((rid, kind), DeviceStatus(kind, rid))

    async def restore(self, rid: int) -> BedTracker:
        """Once per resident: the stored calibration and whether the last bed event was an exit, so a backend
        restart doesn't lose either."""
        tracker = self.tracker(rid)
        if rid in self._restored or not get_settings().tiger_configured:
            return tracker
        # Concurrent messages (band + phone after a restart) wait for the one restore instead of stepping a
        # default tracker that the restore would then overwrite.
        async with self._restore_lock:
            if rid in self._restored:
                return tracker
            if time.monotonic() - self._restore_failed.get(rid, -math.inf) < RESTORE_RETRY_S:
                return tracker  # Tiger was just unreachable: don't stall every reading on it
            try:
                stored, row = await asyncio.wait_for(self._load_state(rid), RESTORE_TIMEOUT_S)
            except Exception as exc:  # devices keep working on defaults
                self._restore_failed[rid] = time.monotonic()
                log.warning("ingest: restoring R-%d bed state failed: %s", rid, " ".join(str(exc).split())[:160])
                return tracker
            self._restored.add(rid)
        if stored:
            tracker.cal = Calibration.from_dict(stored)
        if row is not None:
            tracker.in_bed = row[0] == "bed_return"
        return tracker

    async def _load_state(self, rid: int) -> tuple[dict | None, tuple | None]:
        async with tiger.pooled() as conn:
            stored = await tiger.get_meta(conn, calibration_key(rid))
            row = await tiger.fetchone(
                conn,
                """SELECT type FROM events WHERE resident_id = %s AND type IN ('bed_exit', 'bed_return')
                     AND ts > now() - make_interval(hours => %s) AND ts <= now()
                   ORDER BY ts DESC LIMIT 1""",
                (rid, STATE_LOOKBACK_H),
            )
            await conn.commit()
        return stored, row

    def accepts(self, rid: int) -> str | None:
        """None if this resident takes live device data now, else why not."""
        if rid not in LIVE_RESIDENTS:
            return f"R-{rid} is simulated; live devices stream to R-{fac.LIVE_RESIDENT_ID} only"
        if sim_state.simulated_band:
            return "The simulated band is on in Admin, so device data is ignored. Turn it off to use the devices."
        return None

    # ---- readings

    def add_polar(self, rid: int, ts: float, hr: float | None, worn: bool | None) -> None:
        self.rows.append((ts, rid, hr, worn, None, None, SOURCE_POLAR))
        st = self.device(rid, "polar")
        st.last_seen, st.readings = ts, st.readings + 1
        st.last = {"hr": hr, "worn": worn}

    def add_bed(self, rid: int, s: Summary) -> Reading:
        reading = self.tracker(rid).step(s)
        self.rows.append((s.ts, rid, None, None, reading.in_bed, reading.bed_motion, SOURCE_PHONE))
        if reading.transition is not None:
            self._transition(rid, reading.transition)
        st = self.device(rid, "phone")
        st.last_seen, st.readings = s.ts, st.readings + 1
        st.last = {"in_bed": reading.in_bed, "state": reading.state, "level": reading.level,
                   "bed_motion": reading.bed_motion, "energy": round(s.energy, 4)}  # fmt: skip
        return reading

    def set_bed(self, rid: int, in_bed: bool, reason: str = "admin") -> Transition | None:
        """Admin fallback: record an exit/return as if the bed sensor had seen it."""
        now = time.time()
        t = self.tracker(rid).force(in_bed, now, reason)
        if t is not None:
            self.rows.append((now, rid, None, None, in_bed, 0.0, SOURCE_PHONE))
            self._transition(rid, t)
        return t

    def _transition(self, rid: int, t: Transition) -> None:
        payload = t.payload() | ({"source": "admin"} if t.reason == "admin" else {})
        self.events.append((t.ts, rid, t.kind, None, payload))
        self._flush_now.set()
        log.info("ingest: R-%d %s (%s)", rid, t.kind, t.reason)

    async def save_calibration(self, rid: int, cal: Calibration) -> None:
        try:
            async with tiger.pooled() as conn:
                await tiger.set_meta(conn, calibration_key(rid), cal.to_dict())
                await conn.commit()
        except Exception as exc:
            log.warning("ingest: saving R-%d calibration failed: %s", rid, " ".join(str(exc).split())[:160])

    # ---- writer

    async def flush(self, conn) -> int:
        """Write everything buffered in one pipelined transaction; on failure keep it for the next try."""
        async with self._lock:
            rows, events = self.rows, self.events
            self.rows, self.events = [], []
            if not rows and not events:
                return 0
            t0 = time.perf_counter()
            try:
                await tiger.write_device_batch(conn, rows, events)
            except BaseException:
                keep = rows[-MAX_PENDING_ROWS:]
                self.stats.dropped_rows += len(rows) - len(keep)
                self.rows = keep + self.rows
                self.events = events + self.events
                raise
            self.stats.phase = "running"
            self.stats.flushes += 1
            self.stats.rows_written += len(rows)
            self.stats.events_written += len(events)
            self.stats.last_flush_ms = round((time.perf_counter() - t0) * 1000, 1)
        if events:
            await self._after_events(events)
        return len(rows)

    async def _after_events(self, events: list[tuple]) -> None:
        from app.scoring import evaluator as ev  # the evaluator imports the deep tier; keep startup light

        if ev.evaluator is not None:
            ev.evaluator.poke()  # score the fall-prevention rule now, not on the next 2-s tick
        for ts, rid, kind, staff_id, payload in events:
            event = {"ts": datetime.fromtimestamp(ts, UTC).isoformat(), "resident_id": rid, "type": kind,
                     "staff_id": staff_id, "payload": payload}  # fmt: skip
            await hub.broadcast({"type": "event", "ts": utc_now_iso(), "event": event})

    async def run(self) -> None:
        """Every FLUSH_INTERVAL_S (at once after a bed transition) on a dedicated connection, so a busy API pool
        never delays a bed exit."""
        backoff = 1.0
        while True:
            try:
                async with tiger.connect(application_name=APP_NAME) as conn:
                    self.stats.phase = "running"
                    backoff = 1.0
                    while True:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(self._flush_now.wait(), FLUSH_INTERVAL_S)
                        self._flush_now.clear()
                        await self.flush(conn)
            except asyncio.CancelledError:
                raise
            except tiger.TigerNotConfigured:
                self.stats.phase = "disabled"
                return
            except Exception as exc:
                if tiger.cancelling():
                    raise asyncio.CancelledError from exc
                self.stats.phase = "error"
                self.stats.last_error = " ".join(str(exc).split())[:200]
                log.warning("ingest: write failed (%s) — reconnecting in %.0f s", self.stats.last_error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    # ---- protocol

    async def handle(self, msg: Any, conn: dict) -> dict | None:
        """One device message → an optional reply. `conn` holds this socket's role and resident."""
        if not isinstance(msg, dict):
            raise ValueError("expected a JSON object")
        kind = msg.get("type")
        rid = msg.get("resident_id", conn.get("resident_id"))
        if not isinstance(rid, int) or isinstance(rid, bool):
            raise ValueError("resident_id must be an integer")
        now = time.time()

        if kind == "hello":
            device = msg.get("device")
            if device not in ("polar", "phone"):
                raise ValueError("device must be 'polar' or 'phone'")
            self._register(conn, rid, device)
            tracker = await self.restore(rid)
            if device == "phone" and tracker.calibrating is not None:
                tracker.cancel_calibration()  # a calibration cut off by a dropped socket never finishes
            return {"type": "hello", "resident_id": rid, "device": device, "accepted": self.accepts(rid) is None,
                    "reason": self.accepts(rid), **tracker.describe()}  # fmt: skip

        if kind == "calibrate":
            return await self._calibrate(rid, msg, now)

        if "motion_energy" in msg:
            self._register(conn, rid, "phone")
            energy = _num(msg, "motion_energy", 0, 1000)
            jolt = _num(msg, "peak_jolt", 0, 1000)
            if energy is None:
                raise ValueError("motion_energy is required")
            summary = Summary(
                device_ts(msg.get("ts"), now), energy, jolt if jolt is not None else energy, _gravity(msg)
            )
            if (why := self.accepts(rid)) is not None:
                self.stats.ignored_simulated += 1
                return {"type": "bed", "accepted": False, "reason": why}
            await self.restore(rid)
            r = self.add_bed(rid, summary)
            reply = {"type": "bed", "accepted": True, "ts": summary.ts, "in_bed": r.in_bed, "state": r.state,
                     "level": r.level, "bed_motion": r.bed_motion, "calibrating": r.calibrating,
                     "mode": self.tracker(rid).cal.mode, "calibrated": self.tracker(rid).cal.calibrated,
                     "transition": r.transition.kind if r.transition else None}  # fmt: skip
            self._broadcast(rid, "phone")
            return reply

        if "hr" in msg or "worn" in msg:
            self._register(conn, rid, "polar")
            hr = _num(msg, "hr", 0, 260)
            worn = msg.get("worn")
            if worn is not None and not isinstance(worn, bool):
                raise ValueError("worn must be true, false or null")
            if hr == 0:  # the band reports 0 while it has no reading
                hr = None
            if (why := self.accepts(rid)) is not None:
                self.stats.ignored_simulated += 1
                conn["rejected"] = True
                return {"type": "ack", "accepted": False, "reason": why}
            self.add_polar(rid, device_ts(msg.get("ts"), now), hr, worn)
            self._broadcast(rid, "polar")
            # readings aren't acknowledged one by one; only the end of a rejection is
            return {"type": "ack", "accepted": True} if conn.pop("rejected", False) else None

        raise ValueError("unknown message (expected hello, calibrate, an HR reading or a bed summary)")

    async def _calibrate(self, rid: int, msg: dict, now: float) -> dict:
        tracker = await self.restore(rid)
        action = msg.get("action")
        if action == "begin":
            phase = msg.get("phase")
            if phase not in (*PHASES, PREP):
                raise ValueError(f"phase must be one of {', '.join((*PHASES, PREP))}")
            tracker.begin_calibration(phase)
            return {"type": "calibration", "status": "recording", "phase": phase}
        if action == "cancel":
            tracker.cancel_calibration()
            return {"type": "calibration", "status": "cancelled", **tracker.describe()}
        if action == "finish":
            try:
                cal, t = tracker.finish_calibration(now)
            except ValueError as exc:
                tracker.cancel_calibration()
                return {"type": "calibration", "status": "failed", "error": str(exc), **tracker.describe()}
            if t is not None and self.accepts(rid) is None:
                self.rows.append((now, rid, None, None, True, 0.0, SOURCE_PHONE))
                self._transition(rid, t)
            await self.save_calibration(rid, cal)
            log.info("ingest: R-%d bed sensor calibrated: %s", rid, cal.describe())
            return {"type": "calibration", "status": "done", **tracker.describe()}
        raise ValueError("action must be begin, finish or cancel")

    def _register(self, conn: dict, rid: int, device: str) -> None:
        if conn.get("device") == device and conn.get("resident_id") == rid:
            return
        self.disconnect(conn)
        conn["device"], conn["resident_id"] = device, rid
        self.device(rid, device).sockets += 1

    def disconnect(self, conn: dict) -> None:
        if (device := conn.get("device")) and (rid := conn.get("resident_id")) is not None:
            st = self.device(rid, device)
            st.sockets = max(0, st.sockets - 1)
            tracker = self.trackers.get(rid)
            if device == "phone" and tracker is not None and tracker.calibrating is not None:
                tracker.cancel_calibration()  # else the tracker stays in calibration and detects nothing
        conn.pop("device", None)

    def _broadcast(self, rid: int, kind: str) -> None:
        """Live device reading on /ws/live (the /devices page and R-120's chart), sent in the background so a
        slow dashboard client never delays the device's own socket."""
        st = self.device(rid, kind)
        msg = {"type": "device", "ts": utc_now_iso(), "resident_id": rid, "device": kind,
               "reading_ts": datetime.fromtimestamp(st.last_seen or time.time(), UTC).isoformat(), **st.last}  # fmt: skip
        task = asyncio.create_task(hub.broadcast(msg), name=f"device-broadcast-{rid}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def status(self) -> dict:
        now = time.time()
        return {
            "devices": [d.to_dict(now) for d in self.devices.values()],
            "bed": {rid: t.describe() for rid, t in self.trackers.items()},
            "stats": self.stats.to_dict(),
        }


ingest = Ingest()

router = APIRouter()


@router.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket) -> None:
    await ws.accept()
    conn: dict = {}
    try:
        while True:
            raw = await ws.receive_text()
            try:
                reply = await ingest.handle(json.loads(raw), conn)
            except (ValueError, json.JSONDecodeError) as exc:
                reply = {"type": "error", "message": str(exc)}
            if reply is not None:
                await ws.send_json(reply)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        ingest.disconnect(conn)


def start() -> asyncio.Task | None:
    settings = get_settings()
    if not (settings.device_ingest_enabled and settings.tiger_configured):
        ingest.stats.phase = "disabled"
        return None
    return asyncio.create_task(ingest.run(), name="device-ingest")
