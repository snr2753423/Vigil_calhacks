"""Tiger → Snowflake sync (SPEC §9 ①).

* `bulk_load()`: once per seed (the end of `make seed`, or `make sf-load`): residents, staff, every closed
  `vitals_15m` bucket and every event. The caller has emptied the Snowflake tables (scripts/reset_demo.py).
* `sync_once()`: every minute from the backend: closed `vitals_15m` buckets newer than the watermark, plus
  new events split into CORE.EVENTS and the typed CHECKS / NURSE_NOTES / ALERTS tables. Watermarks and
  cumulative row counts live in Tiger `sync_state`.
* After each sync that closed new buckets, the loop runs ANOMALY_DETECTION on them (app/deep/anomaly.py).

A bucket is synced only once it ended `BUCKET_MARGIN` before the simulator's data frontier, so the
continuous aggregates (refreshed every minute, 1–2 min end offsets) are final and a catch-up backfill after
a restart is never synced half-written. Every Snowflake call is counted in `snowflake_sql.call_stats`.
If Snowflake is down the loop backs off and retries; nothing in the fast tier waits on it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd
import psycopg

from app.config import Settings, get_settings
from app.db import snowflake_sql as sf
from app.db import tiger

log = logging.getLogger(__name__)

APP_NAME = "vigil-sync"
BUCKET = timedelta(minutes=15)
BUCKET_MARGIN = timedelta(minutes=5)  # a bucket is final this long after it ends (cagg refresh latency)
EVENT_MARGIN = timedelta(seconds=30)  # the live loop writes events a few seconds behind wall-clock
VITALS_WM, EVENTS_WM = "vitals_15m", "events"

# Tiger vitals_15m column → Snowflake CORE.VITALS_15M column
VITALS_COLUMNS = {
    "resident_id": "RESIDENT_ID",
    "bucket": "BUCKET_TS",
    "hr_avg": "HR_AVG",
    "hr_min": "HR_MIN",
    "hr_max": "HR_MAX",
    "hr_p50": "HR_P50",
    "hrv_avg": "HRV_AVG",
    "rr_avg": "RR_AVG",
    "rr_min": "RR_MIN",
    "rr_max": "RR_MAX",
    "spo2_min": "SPO2_MIN",
    "skin_temp_avg": "SKIN_TEMP_AVG",
    "motion_sum": "MOTION_SUM",
    "impact_max": "IMPACT_MAX",
    "in_bed_frac": "IN_BED_FRAC",
    "bed_motion_avg": "BED_MOTION_AVG",
    "worn_frac": "WORN_FRAC",
    "n": "N",
}
ALERT_EVENTS = {"alert_raised": "raised", "alert_acknowledged": "acknowledged", "alert_resolved": "resolved"}


# --------------------------------------------------------------------------- pure frame builders


def naive_utc(ts: datetime | None) -> datetime | None:
    """Snowflake ML functions want TIMESTAMP_NTZ: store UTC wall time without an offset."""
    if ts is None or ts.tzinfo is None:  # naive values are already UTC
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


def _ts_series(values: Iterable[datetime | None]) -> pd.Series:
    return pd.Series(pd.to_datetime([naive_utc(v) for v in values]), dtype="datetime64[us]")


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def vitals_frame(rows: list[dict], synced_at: datetime) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(VITALS_COLUMNS)).rename(columns=VITALS_COLUMNS)
    if df.empty:
        return df
    df["BUCKET_TS"] = _ts_series(df["BUCKET_TS"])
    df["RESIDENT_ID"] = df["RESIDENT_ID"].astype("int32")
    df["N"] = df["N"].astype("int64")
    for col in df.columns.difference(["RESIDENT_ID", "BUCKET_TS", "N"]):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["SYNCED_AT"] = _ts_series([synced_at] * len(df))
    return df


def event_frames(rows: list[dict], synced_at: datetime) -> dict[str, pd.DataFrame]:
    """Events → CORE.EVENTS plus the typed CHECKS, NURSE_NOTES and ALERTS tables (JSON as text)."""
    events, checks, notes, alerts = [], [], [], []
    for r in rows:
        p = r["payload"] or {}
        ts, rid, staff = naive_utc(r["ts"]), r["resident_id"], r["staff_id"]
        events.append((ts, rid, r["type"], staff, _json(p)))
        if r["type"] == "check_logged":
            checks.append((ts, rid, staff, p.get("kind"), p.get("reason") or p.get("note"), p.get("source", "sim")))
        elif r["type"] == "note_added":
            notes.append((ts, rid, staff, p.get("author_role"), p.get("source", "text"), bool(p.get("routine")),
                          p.get("text", "")))  # fmt: skip
        elif r["type"] in ALERT_EVENTS:
            alerts.append((ts, rid, ALERT_EVENTS[r["type"]], p.get("tier"), p.get("from_tier"), p.get("score"),
                           p.get("rule"), _json(p.get("factors")), p.get("reason")))  # fmt: skip
    spec = {
        "EVENTS": (events, ["TS", "RESIDENT_ID", "TYPE", "STAFF_ID", "PAYLOAD"]),
        "CHECKS": (checks, ["TS", "RESIDENT_ID", "STAFF_ID", "KIND", "REASON", "SOURCE"]),
        "NURSE_NOTES": (notes, ["TS", "RESIDENT_ID", "STAFF_ID", "AUTHOR_ROLE", "SOURCE", "ROUTINE", "TEXT"]),
        "ALERTS": (alerts, ["TS", "RESIDENT_ID", "EVENT", "TIER", "FROM_TIER", "SCORE", "RULE", "FACTORS", "REASON"]),
    }
    frames = {}
    for table, (data, cols) in spec.items():
        df = pd.DataFrame(data, columns=cols)
        if not df.empty:
            df["TS"] = _ts_series(df["TS"])
            if "STAFF_ID" in df:
                df["STAFF_ID"] = df["STAFF_ID"].astype("Int32")
            df["SYNCED_AT"] = _ts_series([synced_at] * len(df))
        frames[table] = df
    return frames


EVENT_VARIANTS = {"EVENTS": ("PAYLOAD",), "ALERTS": ("FACTORS",)}


def reference_frames(residents: list[dict], staff: list[dict], synced_at: datetime) -> dict[str, pd.DataFrame]:
    res = pd.DataFrame(residents)
    res.columns = [c.upper() for c in res.columns]
    for col in ("DIAGNOSES", "MEDICATIONS", "FLAGS", "EMERGENCY_CONTACT", "PROFILE"):
        res[col] = res[col].map(_json)
    res["SYNCED_AT"] = _ts_series([synced_at] * len(res))
    st = pd.DataFrame(staff)
    st.columns = [c.upper() for c in st.columns]
    st["SYNCED_AT"] = _ts_series([synced_at] * len(st))
    return {"RESIDENTS": res, "STAFF": st}


REFERENCE_VARIANTS = {"RESIDENTS": ("DIAGNOSES", "MEDICATIONS", "FLAGS", "EMERGENCY_CONTACT", "PROFILE"), "STAFF": ()}


def sync_cutoffs(as_of: datetime, frontier: datetime | None) -> tuple[datetime, datetime]:
    """(vitals cutoff, events cutoff): buckets must end by the first, events be at or before the second."""
    edge = min(as_of, frontier) if frontier is not None else as_of
    return edge - BUCKET_MARGIN, edge - EVENT_MARGIN


# --------------------------------------------------------------------------- stats


@dataclass
class SyncStats:
    phase: str = "starting"  # starting | idle | syncing | detecting | waiting for bulk load | busy | error | disabled
    cycles: int = 0
    last_sync: str | None = None
    last_ms: float = 0.0
    rows_synced: dict[str, int] = field(default_factory=dict)  # this process, by Snowflake table
    watermarks: dict[str, str | None] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)  # cumulative per watermark name (Tiger sync_state)
    bulk_load: dict | None = None
    last_error: str | None = None

    def add(self, table: str, n: int) -> None:
        if n:
            self.rows_synced[table] = self.rows_synced.get(table, 0) + n

    def to_dict(self) -> dict:
        return asdict(self)


stats = SyncStats()


def _write_events(sconn, rows: list[dict], synced_at: datetime) -> dict[str, int]:
    out = {}
    for table, df in event_frames(rows, synced_at).items():
        out[table] = sf.write_df(
            sconn, df, table, variant=EVENT_VARIANTS.get(table, ()), feature=f"sync.{table.lower()}"
        )
    return out


# --------------------------------------------------------------------------- bulk load (seed)


async def bulk_load(
    tconn: psycopg.AsyncConnection, sconn, as_of: datetime, say: Callable[[str], None] = log.info
) -> dict[str, int]:
    """Copy reference tables, all closed vitals_15m buckets and all events into empty Snowflake tables, then
    set the watermarks. The caller holds the sync lock and has truncated the Snowflake tables."""
    t0 = time.perf_counter()
    synced_at = datetime.now(UTC)
    vit_cut, ev_cut = sync_cutoffs(as_of, await tiger.sync_frontier(tconn, as_of))
    residents, staff = await tiger.reference_rows(tconn)
    counts: dict[str, int] = {}
    for table, df in reference_frames(residents, staff, synced_at).items():
        counts[table] = await asyncio.to_thread(
            sf.write_df, sconn, df, table, variant=REFERENCE_VARIANTS[table], feature=f"bulk.{table.lower()}"
        )
    vit = await tiger.vitals_15m_closed(tconn, None, vit_cut)
    counts["VITALS_15M"] = await asyncio.to_thread(
        sf.write_df, sconn, vitals_frame(vit, synced_at), "VITALS_15M", feature="bulk.vitals_15m"
    )
    say(f"    VITALS_15M   {counts['VITALS_15M']:,} rows")
    evs = await tiger.events_between(tconn, None, ev_cut)
    counts |= await asyncio.to_thread(_write_events, sconn, evs, synced_at)
    vit_wm = max((r["bucket"] for r in vit), default=None)
    ev_wm = max((r["ts"] for r in evs), default=None)
    await tiger.reset_sync_state(tconn)
    await tiger.set_sync_state(tconn, VITALS_WM, vit_wm, counts["VITALS_15M"])
    await tiger.set_sync_state(tconn, EVENTS_WM, ev_wm, len(evs))
    elapsed = time.perf_counter() - t0
    for k, v in counts.items():
        stats.add(k, v)
    stats.bulk_load = {"at": synced_at.isoformat(), "seconds": round(elapsed, 1), "rows": counts}
    say("    " + ", ".join(f"{k} {v:,}" for k, v in counts.items() if k != "VITALS_15M") + f" ({elapsed:.1f}s)")
    return counts


# --------------------------------------------------------------------------- incremental sync


@dataclass
class SyncResult:
    vitals_rows: int = 0
    event_rows: int = 0
    vitals_from: datetime | None = None  # previous watermark (bucket start)
    vitals_to: datetime | None = None  # new watermark (bucket start)

    @property
    def new_buckets(self) -> bool:
        return self.vitals_to is not None and self.vitals_to != self.vitals_from


async def sync_once(
    tconn: psycopg.AsyncConnection, sconn, as_of: datetime, *, dedupe: bool = False
) -> SyncResult | None:
    """One incremental sync. None = no bulk load yet (nothing to append to). `dedupe` first deletes any
    Snowflake rows past the watermarks, left by a sync that wrote but died before saving its watermark."""
    state = await tiger.get_sync_state(tconn)
    if VITALS_WM not in state or EVENTS_WM not in state:
        return None
    vit_wm, ev_wm = state[VITALS_WM]["watermark"], state[EVENTS_WM]["watermark"]
    vit_cut, ev_cut = sync_cutoffs(as_of, await tiger.sync_frontier(tconn, as_of))
    if dedupe:
        await asyncio.to_thread(_dedupe, sconn, vit_wm, ev_wm)
    synced_at = datetime.now(UTC)
    res = SyncResult(vitals_from=vit_wm, vitals_to=vit_wm)

    vit = await tiger.vitals_15m_closed(tconn, vit_wm, vit_cut)
    if vit:
        res.vitals_rows = await asyncio.to_thread(
            sf.write_df, sconn, vitals_frame(vit, synced_at), "VITALS_15M", feature="sync.vitals_15m"
        )
        res.vitals_to = max(r["bucket"] for r in vit)
        await tiger.set_sync_state(tconn, VITALS_WM, res.vitals_to, res.vitals_rows)
        stats.add("VITALS_15M", res.vitals_rows)

    evs = await tiger.events_between(tconn, ev_wm, ev_cut)
    if evs:
        for table, n in (await asyncio.to_thread(_write_events, sconn, evs, synced_at)).items():
            stats.add(table, n)
        res.event_rows = len(evs)
        await tiger.set_sync_state(tconn, EVENTS_WM, max(r["ts"] for r in evs), len(evs))
    return res


def _dedupe(sconn, vit_wm: datetime | None, ev_wm: datetime | None) -> None:
    if vit_wm is not None:
        sf.query(sconn, "DELETE FROM CORE.VITALS_15M WHERE BUCKET_TS > %s", (naive_utc(vit_wm),), feature="sync.dedupe")
    if ev_wm is not None:
        for table in ("EVENTS", "CHECKS", "NURSE_NOTES", "ALERTS"):
            sf.query(sconn, f"DELETE FROM CORE.{table} WHERE TS > %s", (naive_utc(ev_wm),), feature="sync.dedupe")


# --------------------------------------------------------------------------- background loop


class SyncLoop:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._sconn = None
        self._dedupe = True  # first cycle after start (or an error): clear anything past the watermarks

    def _snowflake(self):
        if self._sconn is None or self._sconn.is_closed():
            self._sconn = sf.connect(self.settings)
        return self._sconn

    def _close_snowflake(self) -> None:
        if self._sconn is not None:
            with contextlib.suppress(Exception):  # already broken
                self._sconn.close()
            self._sconn = None

    async def run(self) -> None:
        from app.deep import anomaly  # here, not at the top: anomaly imports this module

        backoff = self.settings.sync_interval_s
        while True:
            started = time.monotonic()
            try:
                async with tiger.connect(application_name=APP_NAME) as tconn:
                    while True:
                        started = time.monotonic()
                        await self._cycle(tconn, anomaly)
                        backoff = self.settings.sync_interval_s
                        await asyncio.sleep(max(1.0, self.settings.sync_interval_s - (time.monotonic() - started)))
            except asyncio.CancelledError:
                self._close_snowflake()
                raise
            except (tiger.TigerNotConfigured, sf.SnowflakeNotConfigured) as exc:
                stats.phase, stats.last_error = "disabled", str(exc)
                log.warning("sync: %s — Snowflake sync disabled (fast tier keeps running)", exc)
                return
            except Exception as exc:
                if tiger.cancelling():
                    raise asyncio.CancelledError from exc
                stats.phase, stats.last_error = "error", " ".join(str(exc).split())[:200]
                log.warning("sync: %s — retrying in %.0f s", stats.last_error, backoff)
                self._close_snowflake()
                self._dedupe = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 600.0)

    async def _cycle(self, tconn: psycopg.AsyncConnection, anomaly) -> None:
        if not await tiger.try_lock_sync(tconn):  # a seed's bulk load (or another backend) is syncing
            stats.phase = "busy"
            return
        try:
            t0 = time.perf_counter()
            stats.phase = "syncing"
            sconn = await asyncio.to_thread(self._snowflake)
            as_of = datetime.now(UTC)
            res = await sync_once(tconn, sconn, as_of, dedupe=self._dedupe)
            if res is None:
                stats.phase = "waiting for bulk load"  # `make seed` (or `make sf-load`) does the first load
                return
            self._dedupe = False
            stats.cycles += 1
            stats.last_sync = as_of.isoformat()
            stats.last_ms = round((time.perf_counter() - t0) * 1000, 1)
            stats.last_error = None
            state = await tiger.get_sync_state(tconn)
            stats.watermarks = {k: v["watermark"].isoformat() if v["watermark"] else None for k, v in state.items()}
            stats.totals = {k: v["rows_synced"] for k, v in state.items()}
            if res.vitals_rows or res.event_rows:
                log.info("sync: +%d vitals_15m rows, +%d events (%.0f ms)", res.vitals_rows, res.event_rows,
                         stats.last_ms)  # fmt: skip
            if self.settings.anomaly_detect_enabled:
                stats.phase = "detecting"
                await anomaly.detect_pending(tconn, sconn)
            stats.phase = "idle"
        finally:
            await tiger.unlock_sync(tconn)


# --------------------------------------------------------------------------- cumulative call counts

FLUSH_INTERVAL_S = 30.0


async def flush_calls(conn: psycopg.AsyncConnection | None = None) -> None:
    """Add this process's Snowflake calls since the last flush to Tiger `snowflake_calls`. Never raises: the
    counts go back into the buffer and out with the next flush."""
    pending = sf.call_stats.drain()
    if not any(s.calls for s in pending.values()):
        return
    try:
        if conn is not None:
            await tiger.add_snowflake_calls(conn, pending)
        else:
            async with tiger.connect(application_name="vigil-stats") as c:
                await tiger.add_snowflake_calls(c, pending)
    except Exception as exc:
        sf.call_stats.restore(pending)
        log.warning("stats: could not store Snowflake call counts: %s", " ".join(str(exc).split())[:160])


async def flush_loop() -> None:
    try:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            try:
                async with tiger.pooled() as conn:
                    await flush_calls(conn)
            except Exception as exc:  # Tiger unreachable: the counts stay buffered for the next flush
                log.warning("stats: flush skipped: %s", " ".join(str(exc).split())[:160] or type(exc).__name__)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(flush_calls(), 3.0)  # the last calls before a restart
        raise


loop: SyncLoop | None = None


def start() -> asyncio.Task | None:
    global loop
    settings = get_settings()
    if not settings.snowflake_sync_enabled:
        stats.phase = "disabled"
        return None
    if not (settings.snowflake_configured and settings.tiger_configured):
        stats.phase, stats.last_error = "disabled", "Snowflake or Tiger is not configured in .env"
        return None
    loop = SyncLoop(settings)
    return asyncio.create_task(loop.run(), name="snowflake-sync")
