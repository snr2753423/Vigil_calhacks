"""Destructive resets live here (AGENTS.md rule 8), and the one command that gets the demo to a known-good state.

`uv run python scripts/reset_demo.py` (or `make reset`; on the server `scripts/deploy.sh reset`), with no command:
  1. clean the test data the live session left since the seed (typed/voice notes and what they raised, R-120's
     device tests, Mark-checked clicks) from Tiger and Snowflake; simulated history and device vitals are kept,
  2. reset the sim controls in the running backend (replay off, night mode off, injected scenarios cleared),
  3. verify the anomaly models and the prepared replay still match the current seed and re-run only what is missing
     (Prepare replay keeps cached detection, forecasts and cards; never retrains unless `--retrain`, ~67 min),
  4. live checks: Cortex Search, Cortex Analyst (the 5 suggested questions, kept as /ask's offline fallback), a real
     Cortex card for Alvarez at 02:14 (not stored), the recorded night's handoff,
  5. warm the backend caches and every page, then print a READY checklist with PASS / WARN / FAIL per item.
  Options: --retrain · --no-clean · --simulated-band (R-120 simulated until the Polar band works) ·
  --api URL (default http://127.0.0.1:8000) · --site URL (default $PUBLIC_URL in Docker, else http://localhost:8080).
  Exit code 1 if any item fails.

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
import contextlib
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.db import snowflake_sql as sf
from app.db import tiger

SCRIPTS_DIR = Path(__file__).resolve().parent

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


# --------------------------------------------------------------------------- ready: one command to a known-good demo

LIVE_RESIDENT = 120  # the presenter: every event, alert and card after the seed came from a live session
ALVAREZ = 17
STORY_BOOKMARK = "Alvarez at #1"
API_DEFAULT = "http://127.0.0.1:8000"  # the backend itself (`make dev`, or inside the Docker container)
PAGES = ("/", "/resident/17", "/resident/120", "/handoff", "/ask", "/devices", "/under-the-hood", "/admin", "/bed/120")
PAGE_BUDGET_S = 1.0
ALERT_TYPES = ("alert_raised", "alert_acknowledged", "alert_resolved")


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str


def _short(exc: BaseException | str) -> str:
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def _local(ts: datetime) -> str:
    from app.sim import facility as fac

    return ts.astimezone(fac.tzinfo(get_settings().facility_tz)).strftime("%m-%d %H:%M")


async def _count(conn, sql: str, params: tuple) -> int:
    (n,) = await tiger.fetchone(conn, f"WITH d AS ({sql} RETURNING 1) SELECT count(*) FROM d", params)
    return int(n)


async def clean_live_session(conn, seed_end: datetime) -> dict:
    """Remove what testing the live app left in Tiger and Snowflake since the seed ended, keeping the simulator's own
    history (vitals, rounds, meals, routine notes, live alerts for simulated residents) and the device vitals:
    * R-120 (the presenter, never simulated by the live loop): every event (bed exits from phone/admin tests,
      checks, notes, alerts) plus its priority snapshots and Cortex cards, forecasts and note extractions.
    * any resident with a typed or spoken (non-routine) note: from that note on, the notes, the alerts they raised,
      the snapshots, and the cards/forecasts/extractions (what `remove-notes` does).
    * "Mark checked" clicks (`check_logged` with source ui) on anyone else: the check events only.
    * Snowflake: the same rows in CORE, the recorded audio, and handoffs made for shifts after the seed."""
    rows = await tiger.fetch_dicts(
        conn,
        """SELECT resident_id, min(ts) AS since, count(*) AS n FROM events
           WHERE ts > %s AND type = 'note_added' AND NOT coalesce((payload->>'routine')::bool, false)
           GROUP BY resident_id""",
        (seed_end,),
    )
    since = {r["resident_id"]: r["since"] for r in rows}
    since[LIVE_RESIDENT] = seed_end
    out = {"residents": sorted(since), "notes": sum(r["n"] for r in rows), "events": 0, "snapshots": 0,
           "insights": 0, "ui_checks": 0}  # fmt: skip
    async with conn.transaction():
        for rid, t in since.items():
            if rid == LIVE_RESIDENT:
                out["events"] += await _count(conn, "DELETE FROM events WHERE resident_id = %s AND ts > %s", (rid, t))
            else:
                out["events"] += await _count(
                    conn,
                    """DELETE FROM events WHERE resident_id = %s AND ts >= %s
                       AND (type = ANY(%s) OR (type = 'note_added' AND NOT coalesce((payload->>'routine')::bool, false)))""",
                    (rid, t, list(ALERT_TYPES)),
                )
            out["snapshots"] += await _count(
                conn, "DELETE FROM priority_snapshots WHERE resident_id = %s AND ts >= %s", (rid, t)
            )
            out["insights"] += await _count(
                conn,
                """DELETE FROM ai_insights WHERE resident_id = %s AND as_of >= %s
                   AND kind IN ('note_extract', 'forecast', 'assessment')""",
                (rid, t),
            )
        out["ui_checks"] = await _count(
            conn,
            "DELETE FROM events WHERE ts > %s AND type = 'check_logged' AND payload->>'source' = 'ui'",
            (seed_end,),
        )
    if not get_settings().snowflake_configured:
        return out

    def snowflake() -> None:
        sconn = sf.connect()
        try:
            for rid, t in since.items():
                ts = _sf_ts(t)
                if rid == LIVE_RESIDENT:
                    for table in ("EVENTS", "CHECKS", "NURSE_NOTES", "ALERTS"):
                        sf.query(sconn, f"DELETE FROM CORE.{table} WHERE RESIDENT_ID = %s AND TS > %s", (rid, ts),
                                 feature="reset")  # fmt: skip
                else:
                    sf.query(sconn, "DELETE FROM CORE.NURSE_NOTES WHERE RESIDENT_ID = %s AND TS >= %s AND NOT ROUTINE",
                             (rid, ts), feature="reset")  # fmt: skip
                    sf.query(sconn, "DELETE FROM CORE.ALERTS WHERE RESIDENT_ID = %s AND TS >= %s", (rid, ts),
                             feature="reset")  # fmt: skip
                    sf.query(sconn, """DELETE FROM CORE.EVENTS WHERE RESIDENT_ID = %s AND TS >= %s
                                       AND (TYPE LIKE 'alert_%%' OR (TYPE = 'note_added'
                                            AND NOT COALESCE(PAYLOAD:routine::BOOLEAN, FALSE)))""",
                             (rid, ts), feature="reset")  # fmt: skip
                sf.query(sconn, "DELETE FROM CORE.NOTE_EXTRACTS WHERE RESIDENT_ID = %s AND NOTE_TS >= %s", (rid, ts),
                         feature="reset")  # fmt: skip
                for table in ("FORECAST_RESULTS", "AI_ASSESSMENTS"):
                    sf.query(sconn, f"DELETE FROM CORE.{table} WHERE RESIDENT_ID = %s AND AS_OF >= %s", (rid, ts),
                             feature="reset")  # fmt: skip
                sf.query(sconn, f"REMOVE @CORE.NOTE_AUDIO/r{rid:03d}/", feature="reset")
            end = _sf_ts(seed_end)
            sf.query(sconn, "DELETE FROM CORE.CHECKS WHERE TS > %s AND SOURCE = 'ui'", (end,), feature="reset")
            sf.query(sconn, """DELETE FROM CORE.EVENTS WHERE TS > %s AND TYPE = 'check_logged'
                               AND PAYLOAD:source::STRING = 'ui'""", (end,), feature="reset")  # fmt: skip
            # Handoffs for shifts that started after the seed were made from live (test) data; the recorded
            # night's handoff is kept.
            sf.query(sconn, "DELETE FROM CORE.HANDOFFS WHERE SHIFT_DATE > %s::DATE", (end,), feature="reset")
        finally:
            sconn.close()

    await asyncio.to_thread(snowflake)
    out["snowflake"] = True
    return out


async def api_call(client, method: str, path: str, **kw):
    resp = await client.request(method, path, **kw)
    resp.raise_for_status()
    return resp.json()


async def reset_controls(api: str, simulated_band: bool, cleaned: list[int]) -> tuple[Check, dict | None]:
    """Through the running backend (the sim state lives in its process): leave replay, POST /api/sim/reset (night
    mode, clock offset, injected scenarios, pause), optionally the simulated band, then /api/sim/reload."""
    import httpx

    try:
        async with httpx.AsyncClient(base_url=api, timeout=15) as c:
            health = await api_call(c, "GET", "/api/health")
            if (health.get("sim") or {}).get("mode") == "replay":
                await api_call(c, "POST", "/api/replay/stop")
            res = await api_call(c, "POST", "/api/sim/reset")
            if simulated_band:
                await api_call(c, "POST", "/api/sim/simulated_band", json={"enabled": True})
            reload = await api_call(c, "POST", "/api/sim/reload", json={"resident_ids": cleaned})
            health = await api_call(c, "GET", "/api/health")
    except Exception as exc:  # noqa: BLE001
        return Check("Backend", "FAIL", f"{api} not reachable ({_short(exc)}): start it (make dev / deploy push)"), None
    parts = [f"{res.get('injections_cleared', 0)} injections cleared", "night mode off",
             "simulated band " + ("ON" if simulated_band else "off"), f"{reload.get('gate_reset', 0)} gates reset"]  # fmt: skip
    return Check("Sim controls", "PASS", " · ".join(parts)), health


def backend_check(api: str, health: dict) -> Check:
    live, ev, warm = health.get("live_sim") or {}, health.get("evaluator") or {}, health.get("warmup") or {}
    detail = f"{api} · mode {health['sim']['mode']} · live loop {live.get('phase')} · evaluator {ev.get('phase')}"
    if ev.get("avg_ms") is not None:
        detail += f" ({ev['avg_ms']:.0f} ms/cycle)"
    if warm.get("phase") == "done":
        detail += f" · warm-up {warm.get('ms', 0) / 1000:.1f} s"
    if live.get("phase") == "standby":
        return Check("Backend", "WARN", detail + " · ANOTHER backend holds the live lease (stop the other one: "
                                                "make dev on a laptop, or the server)")  # fmt: skip
    if live.get("phase") not in ("streaming", "backfill") or ev.get("phase") != "running":
        return Check("Backend", "FAIL", detail)
    last = live.get("last_tick")
    lag = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() if last else None
    if lag is not None:
        detail += f" · simulator {lag:.0f} s behind"
    return Check("Backend", "PASS" if lag is None or lag < 60 else "WARN", detail)


async def tiger_check(conn, seed: dict) -> Check:
    (ver,) = await tiger.fetchone(conn, "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    (rows,) = await tiger.fetchone(conn, "SELECT approximate_row_count('vitals')")
    (last,) = await tiger.fetchone(conn, "SELECT max(ts) FROM vitals WHERE ts > now() - INTERVAL '1 hour'")
    detail = f"TimescaleDB {ver} · {rows / 1e6:.1f} M vitals rows · {seed['days']}-day seed (seed {seed['seed']})"
    if last is None:
        return Check("Tiger", "WARN", detail + " · no vitals in the last hour (is the backend streaming?)")
    return Check("Tiger", "PASS", detail + f" · newest row {(datetime.now(UTC) - last).total_seconds():.0f} s ago")


async def ensure_models(retrain: bool, say) -> Check:
    """Valid = `make train` ran for this seed and all 5 models exist in VIGIL.ML. Missing metadata but valid models
    (e.g. a lost connection after training): re-register them (`train_models.py --no-train`, seconds). Retrain only
    with --retrain (~67 min)."""
    from app.deep import anomaly

    async def state() -> tuple[dict | None, list[str]]:
        async with tiger.pooled() as conn:
            meta = await anomaly.models_meta(conn)
        rows = await snowflake_rest_client().sql_dicts("SHOW SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA VIGIL.ML",
                                                       feature="sql_api.reset_check")  # fmt: skip
        have = {r.get("name") for r in rows}
        return meta, [name for name, _ in anomaly.MODELS.values() if name not in have]

    try:
        meta, missing = await state()
        if meta is None or missing:
            if not missing:
                say("  models       metadata missing: re-registering the models already in Snowflake …")
                await run_script("train_models.py", "--no-train", "--no-detect")
                meta, missing = await state()
            if (meta is None or missing) and retrain:
                say("  models       --retrain: training the 5 models (~67 min), then detecting …")
                await run_script("train_models.py")
                meta, missing = await state()
    except Exception as exc:  # noqa: BLE001
        return Check("Anomaly models", "FAIL", _short(exc))
    if meta is None or missing:
        why = f"missing in VIGIL.ML: {', '.join(missing)}" if missing else "not trained for the current seed"
        return Check("Anomaly models", "FAIL", f"{why} → run again with --retrain (~67 min)")
    trained = datetime.fromisoformat(meta["trained_at"])
    return Check("Anomaly models", "PASS", f"5 models × 120 series · trained {_local(trained)} on "
                                           f"{meta['days']:.1f} days (last night held out)")  # fmt: skip


async def run_script(name: str, *args: str) -> None:
    proc = await asyncio.create_subprocess_exec(sys.executable, "-u", str(SCRIPTS_DIR / name), *args)
    if await proc.wait() != 0:
        raise RuntimeError(f"{name} {' '.join(args)} exited with {proc.returncode}")


async def ensure_replay(say) -> Check:
    """Valid = prepared for this seed, finished, from the current models, every card made, and the replay steps are
    in Tiger. Otherwise run Prepare replay, which redoes only what is missing (detection only when the night lacks
    results from the current models; cached forecasts, extractions and cards are kept)."""
    from app.deep import anomaly
    from app.sim import prepare
    from app.sim.replay import load_night, load_prepared

    async def problems() -> tuple[list[str], dict | None]:
        async with tiger.pooled() as conn:
            p = await load_prepared(conn)
            meta = await anomaly.models_meta(conn)
            if p is None or p.get("in_progress"):
                return ["not prepared for this seed" if p is None else "a prepare run did not finish"], None
            (steps,) = await tiger.fetchone(conn, "SELECT count(DISTINCT ts) FROM replay_snapshots WHERE ts >= %s",
                                            (datetime.fromisoformat(p["anchor"]),))  # fmt: skip
        out = []
        if meta and p["detection"].get("models_trained_at") != meta["trained_at"]:
            out.append("prepared with older anomaly models")
        if (p.get("assessments_summary") or {}).get("failed"):
            out.append(f"{p['assessments_summary']['failed']} AI cards failed")
        if steps < p["gated"]["steps"]:
            out.append(f"{steps} of {p['gated']['steps']} replay steps in Tiger")
        return out, p

    try:
        issues, p = await problems()
        if issues:
            say(f"  replay       {'; '.join(issues)}: running Prepare replay (only what's missing) …")
            await prepare.prepare(say=lambda m: say(f"  {m}"))
            issues, p = await problems()
    except Exception as exc:  # noqa: BLE001
        return Check("Replay prepared", "FAIL", _short(exc))
    if issues or p is None:
        return Check("Replay prepared", "FAIL", "; ".join(issues))
    async with tiger.pooled() as conn:
        night = await load_night(conn)
    detail = (f"night of {night.date} · {p['gated']['steps']} steps · {p['gated']['alerts']} alerts · "
              f"{p['assessments_summary'].get('planned', 0)} AI cards")  # fmt: skip
    mark = next((b for b in night.bookmarks if b["label"] == STORY_BOOKMARK), None)
    rows = [r for r in p["cast"].get(str(ALVAREZ), []) if mark and r[0] <= mark["ts"]]
    if not rows:
        return Check("Replay prepared", "WARN", detail + " · Alvarez has no rank at the 02:14 bookmark")
    _, tier, score, rank = rows[-1]  # her rank (recorded on changes) and, below, the step's own score
    async with tiger.pooled() as conn:
        step = await tiger.fetchone(conn, "SELECT tier, score FROM replay_snapshots WHERE resident_id = %s AND ts = %s",
                                    (ALVAREZ, night.snap(datetime.fromisoformat(mark["ts"]))))  # fmt: skip
    if step is not None:
        tier, score = step[0], round(step[1])
    story = f"Alvarez #{rank} {tier} {score} at 02:14"
    return Check("Replay prepared", "PASS" if rank == 1 else "WARN", f"{detail} · {story}")


async def search_check() -> Check:
    t0 = time.perf_counter()
    try:
        chunks = await snowflake_rest_client().search_care_plan(LIVE_RESIDENT, "fall precautions bed alarm assist",
                                                                limit=4, timeout=15)  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        return Check("Care-plan search", "FAIL", _short(exc))
    ms = (time.perf_counter() - t0) * 1000
    hit = next((c for c in chunks if "bed alarm" in c["chunk"].lower()), None)
    if hit is None:
        return Check("Care-plan search", "FAIL", f"{len(chunks)} chunks for R-120, none mentions 'bed alarm'")
    line = next((ln.strip() for ln in hit["chunk"].splitlines() if "bed alarm" in ln.lower()), hit["chunk"])[:70]
    return Check("Care-plan search", "PASS", f"Cortex Search REST · R-120 → “{line}” ({ms:.0f} ms)")


async def analyst_check() -> Check:
    """All suggested questions through Cortex Analyst + the SQL API; each answer is also kept as /ask's offline
    fallback."""
    from app.deep import analyst

    t0 = time.perf_counter()
    results: list = []
    for q in analyst.SUGGESTED:  # one at a time, as on /ask (in parallel they queue on the XSMALL warehouse)
        try:
            results.append(await analyst.ask(q))
        except Exception as exc:  # noqa: BLE001
            results.append(exc)
    ok = []
    for r in results:
        if isinstance(r, BaseException) or r.get("error") or not r.get("sql"):
            continue
        ok.append(r)
        await analyst.remember(r)
    errors = [_short(r) if isinstance(r, BaseException) else (r.get("error") or "no SQL") for r in results
              if isinstance(r, BaseException) or r.get("error") or not r.get("sql")]  # fmt: skip
    first = next((r for r in ok if r["question"] == analyst.SUGGESTED[0]), None)
    detail = f"{len(ok)}/{len(results)} suggested questions answered in {time.perf_counter() - t0:.1f} s"
    if first is not None:
        detail += f" · “{first['question'][:38]}…” → {first['row_count']} rows"
    if errors:
        return Check("Cortex Analyst", "FAIL" if not ok else "WARN", detail + f" · {errors[0]}")
    return Check("Cortex Analyst", "PASS", detail)


async def card_check() -> Check:
    """A real AI card, end to end and not stored: Alvarez at the 02:14 bookmark (Tiger context + Cortex Search +
    Cortex completion + validation)."""
    from app.deep import assess
    from app.sim.replay import load_night

    async with tiger.pooled() as conn:
        night = await load_night(conn)
    mark = next((b for b in night.bookmarks if b["label"] == STORY_BOOKMARK), None) if night else None
    if mark is None:
        return Check("Cortex card", "FAIL", "no recorded night")
    t0 = time.perf_counter()
    try:
        card, _, completion, _, _ = await assess.get_assessor().generate(ALVAREZ, datetime.fromisoformat(mark["ts"]))
    except Exception as exc:  # noqa: BLE001
        return Check("Cortex card", "FAIL", f"{_short(exc)} (cards fall back to the template)")
    return Check("Cortex card", "PASS", f"{completion.model} via {completion.via} · R-17 at 02:14 · "
                                        f"“{card['headline'][:60]}” · {len(card['care_plan_citations'])} care-plan "
                                        f"citations ({time.perf_counter() - t0:.0f} s)")  # fmt: skip


async def handoff_check() -> Check:
    from app.deep import handoff
    from app.sim import facility as fac
    from app.sim.replay import load_night

    async with tiger.pooled() as conn:
        night = await load_night(conn)
        day = date.fromisoformat(night.date)
        stored = await handoff.cached("night", day)
        if stored is None:
            stored = await handoff.generate(conn, "night", day, fac.tzinfo(get_settings().facility_tz))
    names = ", ".join(f["name"].split()[-1] for f in (stored.get("flagged") or [])[:6])
    detail = f"night of {night.date} · {stored.get('model')} · flags {names}"
    if stored.get("fallback_reason"):
        return Check("Shift handoff", "WARN", f"{detail} · AI_AGG failed: {stored['fallback_reason']}")
    return Check("Shift handoff", "PASS", detail)


async def warm_pages(api: str, site: str) -> Check:
    """GET the slow API reads (fills the backend's caches) and every page through the public origin."""
    import httpx

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for path in ("/api/stats/outcome", "/api/stats/snowflake?refresh=true", "/api/stats/tiger", "/api/replay",
                     "/api/residents", "/api/queue", "/api/handoff?shift=night"):  # fmt: skip
            with contextlib.suppress(Exception):
                await c.get(api + path)
        times, bad = {}, []
        for path in PAGES:
            for _ in range(2):  # the first request of a page may compile/cache; the second is what a visitor sees
                t0 = time.perf_counter()
                try:
                    r = await c.get(site + path)
                    ok = r.status_code == 200
                except Exception:  # noqa: BLE001
                    ok = False
                times[path] = time.perf_counter() - t0
            if not ok:
                bad.append(path)
    slow = max(times, key=times.get)
    detail = f"{len(PAGES) - len(bad)}/{len(PAGES)} pages via {site} · slowest {slow} {times[slow] * 1000:.0f} ms"
    if bad:
        return Check("Pages", "FAIL", detail + f" · failed: {', '.join(bad)}")
    return Check("Pages", "PASS" if times[slow] < PAGE_BUDGET_S else "WARN", detail)


def snowflake_rest_client():
    from app.db import snowflake_rest

    return snowflake_rest.client()


async def snowflake_login() -> Check:
    t0 = time.perf_counter()

    def connector() -> str:
        sconn = sf.connect()
        try:
            return sf.query(sconn, "SELECT CURRENT_WAREHOUSE()", feature="reset_check")[0][0]
        finally:
            sconn.close()

    try:
        wh = await asyncio.to_thread(connector)
        await snowflake_rest_client().sql("SELECT 1", feature="sql_api.reset_check", timeout=15)
    except Exception as exc:  # noqa: BLE001
        return Check("Snowflake", "FAIL", _short(exc))
    s = get_settings()
    return Check("Snowflake", "PASS", f"key-pair login (connector + REST JWT) · {s.snowflake_account} · {wh} · "
                                      f"{(time.perf_counter() - t0):.1f} s")  # fmt: skip


async def ready(args: argparse.Namespace) -> int:
    from app.deep import sync

    settings = get_settings()
    checks: list[Check] = []
    started = time.perf_counter()

    def say(msg: str) -> None:
        print(msg, flush=True)

    say(f"reset_demo: getting Vigil to a known-good demo state ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)")
    try:
        async with tiger.pooled() as conn:
            seed = await tiger.get_meta(conn, "seed")
            if not seed:
                raise SystemExit("reset_demo: no seed in Tiger — run `make seed DAYS=14` first")
            seed_end = datetime.fromisoformat(seed["end"])
            checks.append(await tiger_check(conn, seed))
            if args.no_clean:
                say("1. clean        skipped (--no-clean)")
                cleaned: list[int] = []
            else:
                say(f"1. clean        live-session test data since the seed ended ({_local(seed_end)} facility time)")
                res = await clean_live_session(conn, seed_end)
                cleaned = res["residents"]
                say(f"               residents {', '.join(f'R-{r}' for r in cleaned)}: {res['notes']} typed/voice notes, "
                    f"{res['events']} events, {res['snapshots']} snapshots, {res['insights']} cards/forecasts/"
                    f"extractions; {res['ui_checks']} Mark-checked clicks"
                    + (" · same in Snowflake CORE + audio + later handoffs" if res.get("snowflake") else ""))  # fmt: skip
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Tiger", "FAIL", _short(exc)))
        return report(checks, started)

    say(f"2. controls     via {args.api}")
    control, health = await reset_controls(args.api, args.simulated_band, cleaned)
    checks.append(control)
    if health is not None:
        checks.append(backend_check(args.api, health))

    if not settings.snowflake_configured:
        checks.append(Check("Snowflake", "FAIL", "SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER not set in .env"))
        return report(checks, started)
    say("3. snowflake    login, models, prepared replay")
    login = await snowflake_login()
    checks.append(login)
    if login.status == "FAIL":
        return report(checks, started)
    checks.append(await ensure_models(args.retrain, say))
    checks.append(await ensure_replay(say))

    say("4. live checks  Cortex Search, Cortex Analyst (5 questions), a Cortex card, the handoff — in parallel")
    results = await asyncio.gather(search_check(), analyst_check(), card_check(), handoff_check(),
                                   return_exceptions=True)  # fmt: skip
    names = ("Care-plan search", "Cortex Analyst", "Cortex card", "Shift handoff")
    for name, r in zip(names, results, strict=True):
        checks.append(Check(name, "FAIL", _short(r)) if isinstance(r, BaseException) else r)

    say(f"5. warm         backend caches + pages via {args.site}")
    checks.append(await warm_pages(args.api, args.site))
    with contextlib.suppress(Exception):
        await sync.flush_calls()  # this run's Snowflake calls → /under-the-hood totals
    return report(checks, started)


def report(checks: list[Check], started: float) -> int:
    print("\nREADY checklist")
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"  {c.status:<4}  {c.name:<{width}}  {c.detail}")
    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]
    took = time.perf_counter() - started
    if fails:
        print(f"\nNOT READY — {len(fails)} failed: {', '.join(c.name for c in fails)} ({took:.0f} s)")
        return 1
    print(f"\nREADY — {len(checks) - len(warns)}/{len(checks)} PASS"
          + (f", {len(warns)} warning(s)" if warns else "") + f" ({took:.0f} s)")  # fmt: skip
    return 0


async def ready_main(args: argparse.Namespace) -> int:
    from app.db import snowflake_rest
    from app.deep import assess

    try:
        return await ready(args)
    finally:
        with contextlib.suppress(Exception):
            await assess.get_assessor().drain()
        with contextlib.suppress(Exception):
            await snowflake_rest.client().aclose()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(sf.close_all)  # e.g. the Analyst semantic-model upload's shared connection
        with contextlib.suppress(Exception):
            await tiger.close_pool()


def _utc(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrain", action="store_true", help="retrain the anomaly models if invalid (~67 min)")
    parser.add_argument("--no-clean", action="store_true", help="keep the live session's test data")
    parser.add_argument("--simulated-band", action="store_true", help="simulate R-120 (no Polar band / iPhone)")
    parser.add_argument("--api", default=API_DEFAULT, help=f"the running backend (default {API_DEFAULT})")
    parser.add_argument("--site", default=(os.environ.get("PUBLIC_URL") or "http://localhost:8080").rstrip("/"),
                        help="the public origin to warm pages through")  # fmt: skip
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
        if not get_settings().tiger_configured:
            print("reset_demo: TIGER_DATABASE_URL is not set in .env", file=sys.stderr)
            return 1
        return asyncio.run(ready_main(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
