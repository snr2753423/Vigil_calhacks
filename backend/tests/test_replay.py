"""Replay (SPEC §4): the grid, seeking, card reveal by as_of, the prepare plan, outcome metrics, no Cortex."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.db import snowflake_sql as sf
from app.db import tiger
from app.scoring import evaluator as ev
from app.scoring import priority
from app.scoring.evaluator import Queue, QueueItem, ScoringContext
from app.sim import outcome, prepare, replay
from app.sim.cast import bookmarks
from app.sim.replay import STEP, Night, ReplayEngine, ReplayError, headline_at
from app.state import sim_state
from app.ws.hub import hub
from tests.scoring_helpers import TZ, baselines, features, profile

START = datetime(2026, 10, 17, 5, 0, tzinfo=UTC)  # 22:00 facility time (PDT)
END = datetime(2026, 10, 17, 13, 30, tzinfo=UTC)  # 06:30
NIGHT = Night("2026-10-16", START, END, tuple(bookmarks(datetime(2026, 10, 16).date(), TZ)))


def at(hhmm: str) -> datetime:
    """Facility time on the replay night → UTC."""
    h, m = map(int, hhmm.split(":"))
    day = 16 if h >= 12 else 17
    return datetime(2026, 10, day, h, m, tzinfo=TZ).astimezone(UTC)


# --------------------------------------------------------------------------- grid and bookmarks


def test_grid_starts_an_hour_early_and_steps_two_minutes() -> None:
    grid = NIGHT.grid()
    assert grid[0] == at("21:00") and grid[1] - grid[0] == STEP == timedelta(minutes=2)
    assert grid[-1] == END and at("22:00") in grid and at("02:14") in grid


def test_seek_snaps_down_to_the_grid_inside_the_night() -> None:
    assert NIGHT.snap(at("01:45")) == at("01:44")
    assert NIGHT.snap(at("02:14")) == at("02:14")
    assert NIGHT.snap(at("04:05")) == at("04:04")
    assert NIGHT.snap(at("20:00")) == START  # before the night → 22:00
    assert NIGHT.snap(END + timedelta(hours=2)) == END


def test_bookmarks_follow_the_gated_replay_timeline() -> None:
    marks = {b["label"]: datetime.fromisoformat(b["ts"]) for b in NIGHT.bookmarks}
    assert marks["Alvarez first deviation (WATCH)"] == at("23:12")
    assert marks["Alvarez reaches HIGH"] == at("01:34")
    assert marks["Alvarez at #1"] == at("02:14")
    assert marks["Alvarez confusion noted"] == at("02:20")
    assert marks["Lee forecast warning"] == at("02:30")
    assert marks["Patel immobility"] == at("03:20") and marks["Ortiz fall"] == at("04:05")
    times = [b["ts"] for b in NIGHT.bookmarks]
    assert times == sorted(times)


# --------------------------------------------------------------------------- prepared cards are revealed by as_of


def card(hhmm: str, text: str) -> dict:
    return {"as_of": at(hhmm).isoformat(), "headline": text}


def test_headline_only_once_the_cursor_reaches_it_and_while_fresh() -> None:
    cards = [card("01:34", "first"), card("02:14", "second")]
    assert headline_at(cards, at("01:32")) is None
    assert headline_at(cards, at("01:34"))["text"] == "first"
    assert headline_at(cards, at("02:12"))["text"] == "first"
    assert headline_at(cards, at("02:14"))["text"] == "second"
    assert headline_at(cards, at("03:15")) is None  # older than the 60-min card age limit


# --------------------------------------------------------------------------- prepare plan


def alert(hhmm: str, rid: int, tier: str = "HIGH", rule: str | None = None) -> dict:
    return {"ts": at(hhmm).isoformat(), "resident_id": rid, "tier": tier, "from_tier": "WATCH", "rule": rule,
            "new_rules": [rule] if rule else []}  # fmt: skip


def test_plan_has_every_bookmark_and_each_escalation() -> None:
    plan = prepare.plan_assessments(list(NIGHT.bookmarks), [alert("00:42", 17), alert("02:42", 17, "CRITICAL")])
    triggers = [(p["resident_id"], p["as_of"], p["trigger"]) for p in plan]
    assert (17, at("02:14"), "bookmark") in triggers
    assert (17, at("00:42"), "escalation") in triggers and (17, at("02:42"), "escalation") in triggers
    assert [p["as_of"] for p in plan] == sorted(p["as_of"] for p in plan)


def test_a_score_alert_right_after_a_card_shares_it_but_a_new_rule_never_does() -> None:
    marks = [b for b in NIGHT.bookmarks if b["resident_id"] == 110]  # Ortiz 04:05
    plan = prepare.plan_assessments(marks, [alert("04:06", 110, "CRITICAL", "fall_prevention"),
                                            alert("04:08", 110, "CRITICAL", "fall"), alert("04:10", 42)])  # fmt: skip
    assert [(p["as_of"], p["label"]) for p in plan if p["resident_id"] == 110] == [
        (at("04:05"), "Ortiz fall"), (at("04:06"), "fall_prevention"), (at("04:08"), "fall")]  # fmt: skip
    plan = prepare.plan_assessments([], [alert("00:34", 42), alert("00:44", 42), alert("00:40", 42)])
    assert [p["as_of"] for p in plan] == [at("00:34"), at("00:44")]  # 00:40 is within 10 min of 00:34


def test_plan_cap_keeps_bookmarks_then_the_most_severe() -> None:
    alerts = [alert(f"23:{m:02d}", 50 + m, "HIGH") for m in range(0, 40, 2)] + [alert("03:00", 99, "CRITICAL")]
    plan = prepare.plan_assessments(list(NIGHT.bookmarks), alerts, cap=10)
    assert len(plan) == 10
    assert sum(p["trigger"] == "bookmark" for p in plan) == len(NIGHT.bookmarks)
    assert any(p["resident_id"] == 99 for p in plan)


# --------------------------------------------------------------------------- outcome metrics


TRANS = [[at("22:26").isoformat(), "WATCH", 36], [at("22:48").isoformat(), "STABLE", 22],
         [at("00:42").isoformat(), "HIGH", 61], [at("00:56").isoformat(), "WATCH", 48]]  # fmt: skip


def test_first_reaching_and_rounds() -> None:
    assert outcome.first_reaching(TRANS, "WATCH", START) == at("22:26")
    assert outcome.first_reaching(TRANS, "HIGH", START) == at("00:42")
    assert outcome.first_reaching(TRANS, "CRITICAL", START) is None
    assert outcome.next_round(at("00:42"), TZ) == at("02:00")
    assert outcome.next_round(at("23:59"), TZ) == at("00:00")
    assert outcome.next_round(at("02:00"), TZ) == at("02:00")
    assert outcome.minutes(at("00:42"), at("02:00")) == 78


def test_first_number_one_needs_rank_one_at_high() -> None:
    rows = [[at("23:12").isoformat(), "WATCH", 44, 1], [at("01:34").isoformat(), "HIGH", 60, 2],
            [at("02:14").isoformat(), "HIGH", 63, 1]]  # fmt: skip
    assert outcome.first_number_one(rows, START) == at("02:14")


def test_fixed_and_vigil_summaries() -> None:
    fixed = outcome.summarize_fixed([
        {"resident_id": 88, "kind": "spo2_low", "alarms": 18, "first_ts": at("22:10")},
        {"resident_id": 17, "kind": "bed_exit", "alarms": 2, "first_ts": at("22:50")},
        {"resident_id": 17, "kind": "rr_high", "alarms": 3, "first_ts": at("04:02")},
    ])  # fmt: skip
    assert fixed["total"] == 23 and fixed["vital_signs"] == 21 and fixed["bed_exits"] == 2 and fixed["residents"] == 2
    vigil = outcome.summarize_vigil([alert("00:42", 17), alert("04:08", 110, "CRITICAL", "fall")])
    assert vigil == {"total": 2, "residents": 2, "by_tier": {"CRITICAL": 1, "HIGH": 1}, "rule_alerts": 1,
                     "score_alerts": 1}  # fmt: skip


def test_alvarez_lead_time() -> None:
    prepared = {"transitions": {"17": TRANS}, "cast": {"17": [[at("00:42").isoformat(), "HIGH", 61, 1]]},
                "alerts": [alert("00:42", 17) | {"score": 61, "rank": 1}]}  # fmt: skip
    fixed = [{"resident_id": 17, "kind": "bed_exit", "alarms": 2, "first_ts": at("22:50")},
             {"resident_id": 17, "kind": "rr_high", "alarms": 3, "first_ts": at("04:02")}]  # fmt: skip
    checks = [{"ts": at("22:48"), "payload": {"kind": "assist"}}, {"ts": at("01:10"), "payload": {"kind": "assist"}},
              {"ts": at("02:17"), "payload": {"kind": "round", "round": "02:00"}}]  # fmt: skip
    t = outcome.alvarez_timeline(prepared, fixed, checks, [], NIGHT, TZ)
    assert t["vigil_high"] == at("00:42") and t["scheduled_round"] == at("02:00") and t["round_check"] == at("02:17")
    assert t["first_fixed_vital"] == at("04:02") and t["first_fixed_vital_kind"] == "RR > 24"
    assert t["first_fixed_any"] == at("22:50")  # her assisted toileting exit is a fixed "any bed exit" alarm
    assert t["lead_vs_round_min"] == 78 and t["lead_vs_fixed_min"] == 200
    assert [a["tier"] for a in t["alerts"]] == ["HIGH"]


# --------------------------------------------------------------------------- Snowflake call totals


def test_call_stats_drain_and_restore() -> None:
    s = sf.CallStats()
    s.record("rest.cortex_search", 100, True)
    s.record("rest.cortex_search", 300, False)
    d = s.drain()
    assert d["rest.cortex_search"].calls == 2 and d["rest.cortex_search"].errors == 1
    assert s.drain() == {}  # drained once
    s.restore(d)
    assert s.drain()["rest.cortex_search"].total_ms == 400
    assert s.snapshot()["rest.cortex_search"]["calls"] == 2  # the per-process view is untouched


def test_every_feature_lands_on_one_api_surface() -> None:
    from app.api.stats import feature_text, group_surfaces

    process = {"rest.sql_api.cortex_complete": {"calls": 2, "avg_ms": 12000.0}}
    cumulative = {"rest.sql_api.cortex_complete": {"calls": 30, "errors": 1, "avg_ms": 11000.0},
                  "rest.cortex_search": {"calls": 30, "errors": 0, "avg_ms": 900.0},
                  "anomaly.detect.hr": {"calls": 9, "errors": 0, "avg_ms": 90000.0}}  # fmt: skip
    surfaces = {s["key"]: s for s in group_surfaces(process, cumulative)}
    assert surfaces["sql_api"]["calls"] == 30 and surfaces["sql_api"]["process_calls"] == 2
    assert surfaces["cortex_search"]["calls"] == 30
    assert [f["feature"] for f in surfaces["connector"]["features"]] == ["anomaly.detect.hr"]
    assert surfaces["analyst"]["calls"] == 0
    assert "DETECT_ANOMALIES" in feature_text("anomaly.detect.rr")


# --------------------------------------------------------------------------- the engine (no database)


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch):
    """Tiger and the evaluator's queries faked: residents 1–3, resident 2's score climbs with replay time."""
    sent: list[dict] = []
    snaps: list[dict] = []
    calls = {"evaluate": [], "seed_from": []}
    profiles = {rid: profile(rid) for rid in (1, 2, 3)}
    bl = baselines(1)

    @asynccontextmanager
    async def pooled():
        yield object()

    async def evaluate(conn, as_of, ctx, gate, clk, *, check_scores=None):
        calls["evaluate"].append(as_of)
        items = []
        for rid, p in profiles.items():
            f = features(as_of, rid=rid)
            res = priority.compute(p, f, baselines(rid), clk)
            score = 20 + (as_of - START).total_seconds() / 60 if rid == 2 else res.score
            dec = gate.update(rid, score, rules=[], last_check_ts=None, as_of=as_of)
            items.append(QueueItem(p, f, res, dec, clk))
        items.sort(key=QueueItem.sort_key)
        return Queue(as_of, items)

    async def latest_snapshots(conn, ts, lookback_s=600, table="priority_snapshots"):
        assert table == "replay_snapshots"
        calls["seed_from"].append(ts)
        return snaps

    async def broadcast(msg):
        sent.append(msg)

    async def no_meta(conn):
        return None

    monkeypatch.setattr(tiger, "pooled", pooled)
    monkeypatch.setattr(tiger, "latest_snapshots", latest_snapshots)
    monkeypatch.setattr(ev, "evaluate", evaluate)
    monkeypatch.setattr(ev, "check_scores_at", lambda conn, ts, table="": asyncio.sleep(0, result={}))
    monkeypatch.setattr(ev, "load_context", lambda conn: asyncio.sleep(0, result=ScoringContext(profiles, bl)))
    monkeypatch.setattr(replay, "load_night", lambda conn: asyncio.sleep(0, result=NIGHT))
    monkeypatch.setattr(replay, "load_prepared", lambda conn: asyncio.sleep(0, result={"seeded_at": "x"}))
    monkeypatch.setattr(hub, "broadcast", broadcast)
    monkeypatch.setattr(ReplayEngine, "_load_cards", lambda self, conn: asyncio.sleep(0, result={
        2: [{"as_of": at("01:34").isoformat(), "headline": "Prepared card"}]}))  # fmt: skip
    monkeypatch.setattr(ReplayEngine, "_ensure_task", lambda self: None)
    saved = sim_state.to_dict()
    yield {"sent": sent, "snaps": snaps, "calls": calls}
    for k, v in saved.items():
        setattr(sim_state, k, v)


def queues(sent: list[dict]) -> list[dict]:
    return [m for m in sent if m.get("type") == "queue"]


def test_start_needs_prepare(fake_db, monkeypatch) -> None:
    monkeypatch.setattr(replay, "load_prepared", lambda conn: asyncio.sleep(0, result=None))
    with pytest.raises(ReplayError, match="Prepare replay first"):
        asyncio.run(ReplayEngine().start())
    assert sim_state.mode == "live"


def test_start_seek_step_and_stop(fake_db) -> None:
    fake_db["snaps"].append({"resident_id": 2, "ts": at("01:42"), "tier": "HIGH", "score": 62.0, "factors": []})
    eng = ReplayEngine()

    async def go():
        status = await eng.start(at=at("01:45"), speed=60, play=False)
        assert status["active"] and sim_state.mode == "replay" and status["cursor"] == at("01:44").isoformat()
        assert fake_db["calls"]["seed_from"] == [at("01:44") - timedelta(microseconds=1)]
        q = queues(fake_db["sent"])[-1]
        assert q["mode"] == "replay" and q["as_of"].startswith("2026-10-17T08:44")
        assert q["display_time"].startswith("2026-10-17T01:44")  # facility time, no display offset
        rosa = next(c for c in q["items"] if c["id"] == 2)
        assert rosa["ai"]["text"] == "Prepared card"  # the cached card, revealed (as_of 01:34 ≤ cursor)
        replay_msgs = [m for m in fake_db["sent"] if m.get("type") == "replay"]
        assert replay_msgs[0]["seeked"] is True and replay_msgs[0]["speed"] == 60
        await eng._step(eng.cursor + STEP)
        assert eng.cursor == at("01:46") and sim_state.replay["cursor"] == at("01:46").isoformat()
        await eng.stop()
        assert sim_state.mode == "live" and sim_state.replay is None

    asyncio.run(go())


def test_seek_seeds_the_gate_from_the_prepared_snapshots(fake_db) -> None:
    # The prepare pass left resident 2 at HIGH: after a seek it stays HIGH at once (no persistence wait).
    fake_db["snaps"].append({"resident_id": 2, "ts": at("02:12"), "tier": "HIGH", "score": 62.0, "factors": []})
    eng = ReplayEngine()

    async def go():
        await eng.start(at=at("02:14"), play=False)
        return eng.card(2)

    rosa = asyncio.run(go())
    assert rosa["tier"] == "HIGH" and rosa["rank"] == 1


def test_alerts_go_out_on_steps_not_on_the_seek(fake_db) -> None:
    eng = ReplayEngine()

    async def go():
        await eng.start(at=at("22:40"), play=False)  # resident 2 scores 20 + minutes since 22:00 → 60 at 22:40
        seek_alerts = [m for m in fake_db["sent"] if m.get("type") == "alert"]
        await eng._step(eng.cursor + STEP)
        await eng._step(eng.cursor + STEP)
        return seek_alerts, [m for m in fake_db["sent"] if m.get("type") == "alert"]

    seek_alerts, alerts = asyncio.run(go())
    assert seek_alerts == []
    assert [(a["resident_id"], a["tier"]) for a in alerts] == [(2, "HIGH")]


def test_speeds_are_validated(fake_db) -> None:
    eng = ReplayEngine()
    with pytest.raises(ReplayError):
        asyncio.run(eng.set_speed(45))
    asyncio.run(eng.set_speed(120))
    assert eng.speed == 120 and eng.slot_s == 1.0


def test_replay_never_calls_cortex(client, fake_db, monkeypatch) -> None:
    """Refresh during a replay answers from the prepared cache (or the template), never Snowflake."""
    from app.deep import assess

    def boom(*a, **k):
        raise AssertionError("Cortex must not be called during a replay")

    monkeypatch.setattr(assess.Assessor, "request", boom)
    monkeypatch.setattr(replay, "replay_cursor", lambda: at("02:14"))
    resident = {"resident_id": 17, "name": "Rosa", "code_status": "FULL", "profile": {}}

    async def fetch_dicts(conn, sql, params=None):
        return [resident] if "FROM residents" in sql else []

    async def cached(self, conn, rid, at_, *, live):
        return {"headline": "Prepared 02:14 card", "source": "cortex", "as_of": at_.isoformat()}

    async def current_for(conn, rid, at_, *, live):
        return None

    monkeypatch.setattr(tiger, "fetch_dicts", fetch_dicts)
    monkeypatch.setattr(assess.Assessor, "cached", cached)
    monkeypatch.setattr(ev, "current_for", current_for)
    r = client.post("/api/explain/17")
    assert r.status_code == 200 and r.json()["headline"] == "Prepared 02:14 card" and r.json()["pending"] is False


def test_writes_are_refused_during_a_replay(client, fake_db, monkeypatch) -> None:
    monkeypatch.setattr(sim_state, "mode", "replay")
    assert client.post("/api/checks", json={"resident_id": 17}).status_code == 409
    assert client.post("/api/notes", json={"resident_id": 17, "text": "hi"}).status_code == 409


def test_hhmm_on_the_replay_night() -> None:
    assert NIGHT.at_local("01:45", TZ) == at("01:45")
    assert NIGHT.at_local("23:12", TZ) == at("23:12")
    with pytest.raises(ReplayError):
        NIGHT.at_local("25:00", TZ)


def test_a_failed_start_goes_back_to_live(fake_db, monkeypatch) -> None:
    async def broken(*a, **k):
        raise OSError("Tiger timed out")

    monkeypatch.setattr(ev, "evaluate", broken)
    with pytest.raises(OSError):
        asyncio.run(ReplayEngine().start(at=at("02:14")))
    assert sim_state.mode == "live" and sim_state.replay is None


def test_back_to_live_republishes_the_live_queue_even_when_paused(fake_db, monkeypatch) -> None:
    class PausedLive:  # the live evaluator with the simulator paused: no cycle will run on a poke
        latest = Queue(at("02:14"), [])
        poked = False

        def poke(self, *, republish=False):
            self.poked = republish

    live = PausedLive()
    monkeypatch.setattr(ev, "evaluator", live)
    eng = ReplayEngine()

    async def go():
        await eng.start(at=at("02:14"), play=False)
        await eng.stop()

    asyncio.run(go())
    assert queues(fake_db["sent"])[-1]["mode"] == "live" and live.poked
