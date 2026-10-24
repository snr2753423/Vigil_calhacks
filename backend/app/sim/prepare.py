"""Prepare replay (SPEC §4): precompute every deep-tier result the replay will reveal, so replay never waits on
Snowflake. Run from /admin (POST /api/replay/prepare) or `scripts/prepare_replay.py`.

1. **Anomaly detection.** `SNOWFLAKE.ML.ANOMALY_DETECTION` over last night's 15-min buckets for all residents
   (from an hour before 22:00, so the warm-up is scored too). Skipped when the night already has results from
   the current models (e.g. `make train` detected through it), unless forced. Results land in Tiger
   `ai_insights` stamped with when live would have known them (bucket end + 10 min).
2. **Notes.** `AI_EXTRACT` on the night's non-routine nurse notes (the cast's, e.g. Alvarez 21:30 and 02:20), stamped
   as known 30 s after each note, so the replay scores P_concern / NEWS2 consciousness from real extractions.
3. **Gated pass.** The real evaluator and AlertGate over the night on the replay grid (every 2 min from 21:00),
   as_of each step. Each step's scores are upserted into `replay_snapshots` (a replay seek seeds its gate from
   them), and the pass records tier changes, alerts and the cast's ranks: the escalations below, and the outcome
   metrics on /under-the-hood.
4. **Forecasts.** `SNOWFLAKE.ML.FORECAST` where live would have asked for one: at each entry into HIGH/CRITICAL and
   every 30 min while it lasts (`forecast.plan_replay`), each stamped as known 2 min after the ask. A forecast raises
   P_trend, which can keep a resident HIGH longer, so the gated pass runs again with the forecasts in place, and
   forecasts the new pass asks for are added (at most FORECAST_ROUNDS rounds). Every pass is stored (upserts); the
   last one, which asked for no new forecast, is what replay shows.
5. **AI cards.** Snowflake Cortex assessments at each bookmark and at each escalation into HIGH/CRITICAL,
   generated as_of that moment and cached in `ai_insights` (kind = assessment). Cards already cached are kept.

Everything is keyed by as_of; the replay only shows what has as_of ≤ cursor. The summary is stored in
`sim_meta.replay_prepared` for the current seed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger
from app.deep import anomaly, forecast, notes
from app.deep.assess import get_assessor
from app.deep.sync import BUCKET
from app.scoring import evaluator as ev
from app.scoring.evaluator import ALERT_TIERS, AlertGate
from app.scoring.priority import TIER_RANK
from app.scoring.rules import NightClock
from app.sim.cast import CAST_IDS
from app.sim.replay import PREPARED_KEY, STEP, Night, load_night

log = logging.getLogger(__name__)

DETECT_LEAD = timedelta(minutes=tiger.ANOMALY_LOOKBACK_MIN)  # the first scored step reads results this old
COVERAGE_OK = 0.95  # share of resident-buckets with results from the current models that counts as "detected"
SNAPSHOT_BATCH = 15  # steps per replay_snapshots upsert
ASSESS_DEDUPE = timedelta(minutes=10)  # a score-driven escalation this soon after a resident's last card shares it
MAX_ASSESSMENTS = 40
ASSESS_RETRIES = 1
FORECAST_ROUNDS = 3  # gated pass → forecasts → gated pass … until no new forecast is asked for
NOTES_LEAD = timedelta(hours=6)  # notes this long before the replay grid starts still count (12-h note window)

Say = Callable[[str], None]


class PrepareError(RuntimeError):
    pass


@dataclass
class Progress:
    phase: str = "idle"  # idle | detecting | notes | scoring | forecasting | assessing | done | error
    started_at: str | None = None
    finished_at: str | None = None
    step: int = 0
    total: int = 0
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def begin(self) -> None:
        self.phase, self.started_at, self.finished_at = (
            "detecting",
            datetime.now(UTC).isoformat(timespec="seconds"),
            None,
        )
        self.step, self.total, self.message, self.error = 0, 0, "", None


progress = Progress()
_task: asyncio.Task | None = None


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


# --------------------------------------------------------------------------- 1. anomaly detection


async def detect_night(conn: psycopg.AsyncConnection, night: Night, *, force: bool, say: Say) -> dict:
    meta = await anomaly.models_meta(conn)
    if meta is None:
        raise PrepareError("No Snowflake anomaly models for this seed: run `make train` first.")
    train_to = datetime.fromisoformat(meta["train_to"])
    start, end = max(night.anchor - DETECT_LEAD, train_to), night.end
    expected = len(await tiger.fetchall(conn, "SELECT resident_id FROM residents")) * int((end - start) / BUCKET)
    (have,) = await tiger.fetchone(
        conn,
        """SELECT count(DISTINCT (resident_id, payload->>'bucket')) FROM ai_insights
           WHERE kind = 'anomaly' AND (payload->>'bucket')::timestamptz >= %s
             AND (payload->>'bucket')::timestamptz < %s AND created_at >= %s""",
        (start, end, datetime.fromisoformat(meta["trained_at"])),
    )
    base = {"range": [_iso(start), _iso(end)], "models_trained_at": meta["trained_at"], "expected": expected}
    if not force and expected and have >= COVERAGE_OK * expected:
        say(f"  detect       already done for these models ({have:,} of {expected:,} resident-buckets); skipped")
        return base | {"ran": False, "resident_buckets": have, "reason": "already detected with the current models"}

    progress.message = "Snowflake ANOMALY_DETECTION over last night (5 models × 120 residents)"
    say(f"  detect       {start:%Y-%m-%d %H:%M} → {end:%H:%M} UTC, 5 models in parallel …")
    if not await tiger.try_lock_sync(conn):
        say("  detect       waiting for the Snowflake sync cycle to finish …")
        await tiger.lock_sync(conn)
    try:
        sconn = await asyncio.to_thread(sf.connect)
        try:
            det = await anomaly.detect_range(conn, sconn, start, end, train_to)
        finally:
            await asyncio.to_thread(sconn.close)
    finally:
        await tiger.unlock_sync(conn)
    if not det.ok:
        raise PrepareError(f"anomaly detection failed: {anomaly.stats.last_error}")
    flagged = anomaly.stats.last_flagged
    say(f"  detect       {len(det.results):,} results, {flagged} flagged resident-buckets in "
        f"{anomaly.stats.last_seconds:.0f} s · " + ", ".join(f"{m} {s:.0f}s" for m, s in det.seconds.items()))  # fmt: skip
    return base | {
        "ran": True,
        "results": len(det.results),
        "flagged_buckets": flagged,
        "seconds": anomaly.stats.last_seconds,
        "seconds_by_metric": det.seconds,
    }


# --------------------------------------------------------------------------- 2. gated pass


@dataclass
class GatedPass:
    steps: int
    avg_ms: float
    snapshots: int
    alerts: list[dict]  # every alert the gate raised on the night (22:00–06:30)
    transitions: dict[int, list[list]]  # rid → [[ts, tier, score], …] at each tier change
    cast: dict[int, list[list]]  # cast rid → [[ts, tier, score, rank], …] at each tier or rank change
    entered_high: list[tuple[int, datetime]]  # (rid, ts) entering HIGH/CRITICAL: forecast requests
    cards: dict[tuple[int, datetime], dict]  # (rid, step) → the gated score card, at alerts and bookmarks


async def gated_pass(conn: psycopg.AsyncConnection, night: Night, tz: ZoneInfo, *, say: Say) -> GatedPass:
    """The live evaluator and alert gate over the replay grid, as_of each step (the replay engine's grid); each
    step is upserted into replay_snapshots (a later pass overwrites an earlier one in place)."""
    ctx = await ev.load_context(conn)
    gate, clock = AlertGate(), NightClock(tz=tz)
    grid = night.grid()
    progress.total, progress.message = len(grid), "Scoring last night through the live pipeline"
    marks = {(int(b["resident_id"]), night.snap(datetime.fromisoformat(b["ts"]))) for b in night.bookmarks}
    cards: dict[tuple[int, datetime], dict] = {}
    buf: list[tuple] = []
    snapshots = 0
    tiers: dict[int, str] = {}
    last_cast: dict[int, tuple] = {}
    transitions: dict[int, list[list]] = {}
    cast: dict[int, list[list]] = {}
    alerts: list[dict] = []
    entered: list[tuple[int, datetime]] = []
    total_ms = 0.0
    for i, ts in enumerate(grid):
        t0 = time.perf_counter()
        queue = await ev.evaluate(conn, ts, ctx, gate, clock)
        total_ms += (time.perf_counter() - t0) * 1000
        for rank, it in enumerate(queue.items, 1):
            rid, d = it.profile.resident_id, it.decision
            buf.append((ts, rid, d.score, d.tier, [x.to_dict() for x in it.result.factors]))
            if d.alert or (rid, ts) in marks:
                cards[(rid, ts)] = it.to_dict(rank)  # the card the Cortex assessment explains
            score = round(d.score)
            if tiers.get(rid) != d.tier:
                transitions.setdefault(rid, []).append([_iso(ts), d.tier, score])
                if d.tier in ALERT_TIERS and TIER_RANK[d.tier] > TIER_RANK[tiers.get(rid, "STABLE")]:
                    entered.append((rid, ts))
                tiers[rid] = d.tier
            if rid in CAST_IDS and last_cast.get(rid) != (d.tier, rank):
                cast.setdefault(rid, []).append([_iso(ts), d.tier, score, rank])
                last_cast[rid] = (d.tier, rank)
            if d.alert and ts >= night.start:
                alerts.append({
                    "ts": _iso(ts), "resident_id": rid, "tier": d.tier, "from_tier": d.prev_tier, "score": score,
                    "rank": rank, "rule": d.rule.rule if d.rule else None, "new_rules": list(d.new_rules),
                    "factors": [x.text for x in it.result.factors],
                })  # fmt: skip
        if (i + 1) % SNAPSHOT_BATCH == 0 or i == len(grid) - 1:
            snapshots += await tiger.upsert_replay_snapshots(conn, buf)
            buf = []
        progress.step = i + 1
        if (i + 1) % 30 == 0:
            top = queue.items[0]
            say(f"  score        {ts.astimezone(tz):%H:%M} · #1 {top.profile.name} {top.tier} {top.decision.score:.0f} "
                f"· {len(alerts)} alerts so far")  # fmt: skip
    avg = total_ms / max(len(grid), 1)
    say(f"  score        {len(grid)} steps every {STEP.seconds // 60} min, {avg:.0f} ms each · {len(alerts)} alerts · "
        f"{snapshots:,} snapshots")  # fmt: skip
    return GatedPass(len(grid), round(avg, 1), snapshots, alerts, transitions, cast, entered, cards)


# --------------------------------------------------------------------------- 4. AI cards


def _tick() -> None:
    progress.step += 1


def plan_assessments(
    bookmarks: list[dict], alerts: list[dict], *, dedupe: timedelta = ASSESS_DEDUPE, cap: int = MAX_ASSESSMENTS
) -> list[dict]:
    """Which (resident, as_of) get a Cortex card: every bookmark, and every alert into HIGH/CRITICAL. A
    score-driven alert within `dedupe` after that resident's previous card shares it; a new rule (a fall, a bed
    exit) always gets its own. Over `cap`, bookmarks stay and alerts are kept by tier, then time."""
    plan = [
        {"resident_id": int(b["resident_id"]), "as_of": datetime.fromisoformat(b["ts"]), "trigger": "bookmark",
         "label": b["label"]}
        for b in bookmarks
    ]  # fmt: skip
    escal: list[dict] = []
    for a in sorted(alerts, key=lambda a: a["ts"]):
        ts, rid = datetime.fromisoformat(a["ts"]), int(a["resident_id"])
        if not a.get("new_rules") and any(
            p["resident_id"] == rid and timedelta(0) <= ts - p["as_of"] < dedupe for p in plan + escal
        ):
            continue
        what = a.get("rule") if a.get("new_rules") else f"{a.get('from_tier')}→{a['tier']}"
        escal.append({"resident_id": rid, "as_of": ts, "trigger": "escalation", "label": what, "tier": a["tier"]})
    room = max(cap - len(plan), 0)
    if len(escal) > room:
        escal = sorted(escal, key=lambda e: (-TIER_RANK[e["tier"]], e["as_of"]))[:room]
    return sorted(plan + escal, key=lambda p: (p["as_of"], p["resident_id"]))


async def run_assessments(
    conn: psycopg.AsyncConnection,
    night: Night,
    plan: list[dict],
    *,
    force: bool,
    say: Say,
    cards: dict[tuple[int, datetime], dict] | None = None,
) -> list[dict]:
    existing = {
        (r["resident_id"], r["as_of"])
        for r in await tiger.fetch_dicts(
            conn,
            """SELECT resident_id, as_of FROM ai_insights
               WHERE kind = 'assessment' AND as_of >= %s AND as_of <= %s AND payload->>'source' = 'cortex'""",
            (night.anchor, night.end),
        )
    }
    results = []
    todo = []
    for p in plan:
        row = {**p, "as_of": _iso(p["as_of"])}
        if not force and (p["resident_id"], p["as_of"]) in existing:
            results.append(row | {"status": "cached"})
        else:
            todo.append(row)
    progress.total, progress.step = len(plan), len(results)
    progress.message = f"Snowflake Cortex cards: {len(todo)} to generate, {len(results)} already cached"
    say(f"  cards        {len(plan)} planned · {len(results)} cached · generating {len(todo)} (Cortex, 2 at a time) …")
    assessor = get_assessor()

    async def one(row: dict) -> dict:
        at = datetime.fromisoformat(row["as_of"])
        err = None
        for _ in range(1 + ASSESS_RETRIES):
            t0 = time.perf_counter()
            try:
                gated = (cards or {}).get((row["resident_id"], night.snap(at)))  # what the replay queue shows
                card = await assessor.request(row["resident_id"], at, f"prepare:{row['trigger']}", gated)
                ms = round((time.perf_counter() - t0) * 1000)
                return row | {"status": "cortex", "headline": card["headline"], "model": card["model"], "ms": ms}
            except Exception as exc:
                err = " ".join(str(exc).split())[:160] or type(exc).__name__
        return row | {"status": "failed", "error": err}

    for fut in asyncio.as_completed([one(r) for r in todo]):
        r = await fut
        results.append(r)
        progress.step += 1
        say(f"  card         R-{r['resident_id']} {r['as_of'][11:16]}Z {r['trigger']} {r.get('label', '')}: "
            f"{r['status']} {r.get('headline') or r.get('error') or ''}")  # fmt: skip
    await assessor.drain()  # the Snowflake copies (CORE.AI_ASSESSMENTS) land before prepare reports done
    return sorted(results, key=lambda r: (r["as_of"], r["resident_id"]))


# --------------------------------------------------------------------------- orchestration


async def prepare(*, force_detect: bool = False, force_assess: bool = False, force_deep: bool = False,
                  assess: bool = True, say: Say = log.info) -> dict:  # fmt: skip
    """Run every step and store `sim_meta.replay_prepared`. Returns that summary. `force_deep`: redo the note
    extractions and forecasts already cached for the night."""
    settings = get_settings()
    tz = ZoneInfo(settings.facility_tz)
    t0 = time.perf_counter()
    progress.begin()
    try:
        async with tiger.connect(application_name="vigil-prepare") as conn:
            night = await load_night(conn)
            seed = await tiger.get_meta(conn, "seed")
            if night is None or seed is None:
                raise PrepareError("No recorded night: run `make seed` first.")
            if not settings.snowflake_configured:
                raise PrepareError("Snowflake is not configured (.env): prepare needs it for detection and AI cards.")
            say(f"prepare replay: night of {night.date} · {night.start:%H:%M}–{night.end:%H:%M} UTC")
            # Not "prepared" until this run finishes: a run that fails midway must not leave the previous
            # summary vouching for half-rewritten snapshots (replay then asks for Prepare again).
            await tiger.set_meta(conn, PREPARED_KEY, {"in_progress": True, "started_at": progress.started_at})
            detection = await detect_night(conn, night, force=force_detect, say=say)

            progress.phase, progress.message = "notes", "Snowflake AI_EXTRACT on the night's nurse notes"
            note_summary = await notes.prepare_night_notes(conn, night.anchor - NOTES_LEAD, night.end,
                                                           force=force_deep, say=say)  # fmt: skip
            say(f"  notes        {note_summary['notes']} notes · {note_summary['extracted']} extracted · "
                f"{note_summary['cached']} cached · {note_summary['failed']} failed")  # fmt: skip

            forecasts: dict = {"rounds": []}
            planned: set[tuple[int, datetime]] = set()
            settled = None  # the stored pass that asked for no new forecast (every forecast it uses was in place)
            for rnd in range(1, FORECAST_ROUNDS + 1):
                progress.phase, progress.step = "scoring", 0
                gp = await gated_pass(conn, night, tz, say=say)
                plan = forecast.plan_replay(gp.transitions, night.end, set(CAST_IDS))
                new = [p for p in plan if p not in planned]
                if not new:
                    settled = gp
                    break
                planned.update(new)
                progress.phase, progress.message = "forecasting", f"Snowflake FORECAST, round {rnd}: {len(new)} to make"
                progress.total, progress.step = len(new), 0
                r = await forecast.prepare_replay_forecasts(conn, new, force=force_deep, say=say,
                                                            on_done=_tick)  # fmt: skip
                forecasts["rounds"].append({k: v for k, v in r.items() if k != "callouts"})
                forecasts.setdefault("callouts", []).extend(r.get("callouts") or [])
            forecasts |= {"requested": len(planned), "stored": sum(r.get("stored", 0) for r in forecasts["rounds"]),
                          "cached": sum(r.get("cached", 0) for r in forecasts["rounds"]),
                          "failed": sum(r.get("failed", 0) for r in forecasts["rounds"]),
                          "status": "done", "train_hours": forecast.train_hours()}  # fmt: skip
            say(f"  forecasts    {forecasts['requested']} asked for in {len(forecasts['rounds'])} round(s) · "
                f"{forecasts['stored']} made · {forecasts['cached']} cached · {forecasts['failed']} failed")  # fmt: skip
            if settled is None:  # the last round made forecasts: score once more with them in place
                progress.phase, progress.step = "scoring", 0
                gp = await gated_pass(conn, night, tz, say=say)

            plan = plan_assessments(list(night.bookmarks), gp.alerts)
            progress.phase = "assessing"
            cards = (
                await run_assessments(conn, night, plan, force=force_assess, say=say, cards=gp.cards) if assess else []
            )

            counts = {s: sum(1 for c in cards if c["status"] == s) for s in ("cortex", "cached", "failed")}
            prepared = {
                "seeded_at": seed["seeded_at"],
                "prepared_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "seconds": round(time.perf_counter() - t0, 1),
                "step_s": int(STEP.total_seconds()),
                "anchor": _iso(night.anchor),
                "start": _iso(night.start),
                "end": _iso(night.end),
                "detection": detection,
                "gated": {"steps": gp.steps, "avg_ms": gp.avg_ms, "snapshots": gp.snapshots, "alerts": len(gp.alerts)},
                "alerts": gp.alerts,
                "transitions": {str(k): v for k, v in gp.transitions.items()},
                "cast": {str(k): v for k, v in gp.cast.items()},
                "forecasts": forecasts,
                "notes": note_summary,
                "assessments": cards,
                "assessments_summary": {"planned": len(plan), **counts},
            }
            await tiger.set_meta(conn, PREPARED_KEY, prepared)
        progress.phase, progress.message = "done", f"Prepared in {prepared['seconds']:.0f} s"
        say(f"prepare replay: done in {prepared['seconds']:.0f} s · cards {counts}")
        return prepared
    except Exception as exc:
        progress.phase, progress.error = "error", " ".join(str(exc).split())[:300] or type(exc).__name__
        raise
    finally:
        progress.finished_at = datetime.now(UTC).isoformat(timespec="seconds")


def running() -> bool:
    return _task is not None and not _task.done()


def launch(*, force_detect: bool = False, force_assess: bool = False, force_deep: bool = False) -> None:
    """Start `prepare` in the backend (POST /api/replay/prepare); progress is polled via GET /api/replay."""
    global _task
    if running():
        raise PrepareError("Prepare replay is already running.")

    async def go() -> None:
        from app.sim.replay import get_engine

        try:
            await prepare(force_detect=force_detect, force_assess=force_assess, force_deep=force_deep)
            eng = get_engine()
            await eng.refresh_meta()
            if eng.active:
                await eng.reload_cards()
        except Exception:
            log.exception("prepare replay failed")

    _task = asyncio.create_task(go(), name="prepare-replay")
