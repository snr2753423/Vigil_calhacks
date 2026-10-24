"""WS /ws/ingest protocol, device rows, the 1-s writer, and the live bed exit → Cortex trigger (SPEC §10)."""

import asyncio
import time

import pytest

from app.db import tiger
from app.deep import assess
from app.devices import ingest as ingest_mod
from app.devices.ingest import SOURCE_PHONE, SOURCE_POLAR, Ingest, device_ts
from app.state import sim_state

R120 = 120


@pytest.fixture
def ing(monkeypatch: pytest.MonkeyPatch) -> Ingest:
    monkeypatch.setattr(sim_state, "simulated_band", False)
    i = Ingest()
    i._restored.add(R120)  # no Tiger in unit tests
    return i


def run(coro):
    return asyncio.run(coro)


def test_device_ts_rejects_future_and_stale_clocks():
    now = 1_700_000_000.0
    assert device_ts((now - 2) * 1000, now) == pytest.approx(now - 2)
    assert device_ts("2023-11-14T22:13:18Z", now) == pytest.approx(now - 2)
    assert device_ts((now + 5) * 1000, now) == now  # never a future timestamp
    assert device_ts((now - 60) * 1000, now) == now  # too far behind: use the server's time
    assert device_ts(None, now) == now and device_ts("nonsense", now) == now


def test_polar_reading_becomes_a_row(ing: Ingest):
    conn: dict = {}
    reply = run(ing.handle({"type": "hello", "device": "polar", "resident_id": R120}, conn))
    assert reply["accepted"] and reply["device"] == "polar"
    assert run(ing.handle({"resident_id": R120, "ts": time.time() * 1000, "hr": 74, "worn": True}, conn)) is None
    run(ing.handle({"resident_id": R120, "hr": 0, "worn": False}, conn))  # band off the wrist: no HR
    assert [(r[2], r[3], r[6]) for r in ing.rows] == [(74.0, True, SOURCE_POLAR), (None, False, SOURCE_POLAR)]
    st = ing.device(R120, "polar")
    assert st.sockets == 1 and st.readings == 2 and st.last == {"hr": None, "worn": False}
    ing.disconnect(conn)
    assert st.sockets == 0


def test_bad_messages_raise(ing: Ingest):
    with pytest.raises(ValueError):
        run(ing.handle({"resident_id": R120, "hr": "fast"}, {}))
    with pytest.raises(ValueError):
        run(ing.handle({"resident_id": "120", "hr": 70}, {}))
    with pytest.raises(ValueError):
        run(ing.handle({"resident_id": R120, "motion_energy": 0.1, "gravity": [1, 2]}, {}))
    with pytest.raises(ValueError):
        run(ing.handle({"resident_id": R120, "something": 1}, {}))


def test_only_the_live_resident_takes_device_data(ing: Ingest, monkeypatch: pytest.MonkeyPatch):
    reply = run(ing.handle({"resident_id": 17, "motion_energy": 0.1, "peak_jolt": 0.2}, {}))
    assert reply == {"type": "bed", "accepted": False, "reason": reply["reason"]} and "simulated" in reply["reason"]
    monkeypatch.setattr(sim_state, "simulated_band", True)
    reply = run(ing.handle({"resident_id": R120, "hr": 70, "worn": True}, {}))
    assert reply["accepted"] is False and "simulated band" in reply["reason"]
    assert ing.rows == [] and ing.stats.ignored_simulated == 2


def _summary(energy: float, jolt: float, g=(0.0, 0.0, 9.81)) -> dict:
    return {"resident_id": R120, "motion_energy": energy, "peak_jolt": jolt, "gravity": list(g)}


def test_calibration_then_live_bed_exit_writes_rows_and_event(ing: Ingest, monkeypatch: pytest.MonkeyPatch):
    saved = []

    async def save(rid, cal):
        saved.append((rid, cal))

    monkeypatch.setattr(ing, "save_calibration", save)
    clock = [1_700_000_000.0]
    monkeypatch.setattr(ingest_mod.time, "time", lambda: clock[0])
    conn: dict = {}
    occupied = (0.0, 0.51, 9.80)  # ~3° tipped by the presenter's weight on the pillow

    def send(msg):
        clock[0] += 1
        return run(ing.handle(msg, conn))

    send({"type": "hello", "device": "phone", "resident_id": R120})  # later messages may omit resident_id
    assert send({"type": "calibrate", "action": "begin", "phase": "empty"})["status"] == "recording"
    for _ in range(5):
        assert send(_summary(0.006, 0.015))["calibrating"] == "empty"
    send({"type": "calibrate", "action": "begin", "phase": "still"})
    for _ in range(10):
        send(_summary(0.018, 0.045, occupied))
    send({"type": "calibrate", "action": "begin", "phase": "shift"})
    for _ in range(5):
        send(_summary(0.35, 2.0, occupied))
    done = send({"type": "calibrate", "action": "finish"})
    assert done["status"] == "done" and done["calibration"]["mode"] == "tilt" and done["in_bed"] is True
    assert saved and saved[0][0] == R120

    for _ in range(5):
        r = send(_summary(0.018, 0.045, occupied))
        assert r["in_bed"] is True and r["state"].startswith("in bed")
    send(_summary(0.6, 3.5, occupied))  # gets up
    replies = [send(_summary(0.006, 0.015)) for _ in range(3)]
    assert [r["transition"] for r in replies] == [None, "bed_exit", None]
    assert replies[-1]["in_bed"] is False and replies[-1]["state"] == "out of bed"
    assert [e[2] for e in ing.events] == ["bed_exit"]
    assert ing.events[0][4]["detected_by"] == "tilt"
    assert ing._flush_now.is_set()  # a transition is written at once, not on the next 1-s tick
    phone_rows = [r for r in ing.rows if r[6] == SOURCE_PHONE]
    assert phone_rows[-1][4] is False and phone_rows[0][4] is None  # calibration rows don't claim a state


def test_admin_bed_exit_and_return(ing: Ingest):
    t = ing.set_bed(R120, False)
    assert t.kind == "bed_exit" and ing.events[-1][4]["source"] == "admin"
    assert ing.set_bed(R120, False) is None  # already out: no duplicate event
    assert ing.set_bed(R120, True).kind == "bed_return"
    assert [r[4] for r in ing.rows] == [False, True]


def test_flush_writes_then_pokes_evaluator_and_broadcasts(ing: Ingest, monkeypatch: pytest.MonkeyPatch):
    written, broadcast, poked = {}, [], []

    async def write_device_batch(_conn, rows, events):
        written["rows"] = [(r[2], r[4], r[6]) for r in rows]
        written["events"] = [e[2] for e in events]

    async def bcast(msg):
        broadcast.append(msg)

    class Ev:
        def poke(self):
            poked.append(True)

    from app.scoring import evaluator as ev

    monkeypatch.setattr(tiger, "write_device_batch", write_device_batch)
    monkeypatch.setattr(ingest_mod.hub, "broadcast", bcast)
    monkeypatch.setattr(ev, "evaluator", Ev())

    ing.add_polar(R120, time.time(), 71.0, True)
    ing.set_bed(R120, False)
    assert run(ing.flush(object())) == 2
    assert written == {"rows": [(71.0, None, SOURCE_POLAR), (None, False, SOURCE_PHONE)], "events": ["bed_exit"]}
    assert poked == [True]  # the fall-prevention rule is scored right after the write
    assert broadcast[0]["type"] == "event" and broadcast[0]["event"]["type"] == "bed_exit"
    assert ing.rows == [] and ing.events == [] and ing.stats.rows_written == 2
    assert run(ing.flush(object())) == 0  # nothing buffered: no write


def test_flush_keeps_rows_when_tiger_fails(ing: Ingest, monkeypatch: pytest.MonkeyPatch):
    async def fail(*_):
        raise OSError("tiger down")

    monkeypatch.setattr(tiger, "write_device_batch", fail)
    ing.add_polar(R120, time.time(), 71.0, True)
    ing.set_bed(R120, False)
    with pytest.raises(OSError):
        run(ing.flush(object()))
    assert len(ing.rows) == 2 and len(ing.events) == 1


def test_ws_ingest_round_trip(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sim_state, "simulated_band", False)
    fresh = Ingest()
    fresh._restored.add(R120)
    monkeypatch.setattr(ingest_mod, "ingest", fresh)
    with client.websocket_connect("/ws/ingest") as ws:
        ws.send_json({"type": "hello", "device": "phone", "resident_id": R120})
        assert ws.receive_json()["type"] == "hello"
        ws.send_json(_summary(0.02, 0.05))
        bed = ws.receive_json()
        assert bed["type"] == "bed" and bed["accepted"] and bed["in_bed"] is True
        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"
    assert fresh.device(R120, "phone").sockets == 0 and len(fresh.rows) == 1


def test_devices_status_endpoint(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLIC_URL", "https://example-tunnel.trycloudflare.com/")
    body = client.get("/api/devices").json()
    assert body["bed_url"] == "https://example-tunnel.trycloudflare.com/bed/120"
    assert body["live_resident_id"] == R120 and "devices" in body


# --------------------------------------------------------------------------- live bed exit → Cortex at once


def test_alert_triggers_assessment_immediately_with_debounce(monkeypatch: pytest.MonkeyPatch):
    a = assess.Assessor()
    calls = []
    monkeypatch.setattr(a, "request", lambda rid, as_of, trigger: calls.append((rid, as_of, trigger)) or "task")
    assert a.on_alert(R120, "CRITICAL", rule=True) is None  # automatic triggers off (no Snowflake)
    a.triggers = True
    assert a.on_alert(R120, "CRITICAL", rule=True) == "task"
    assert calls == [(R120, None, "alert")]
    assert a.on_alert(R120, "CRITICAL", rule=True) is None  # debounced
    assert a.seen_tier[R120] == "CRITICAL"  # the 10-s scan won't count the same rise again
    assert a.scan([(R120, "CRITICAL", 1)], assess.datetime.now(assess.UTC)) == []


def test_template_cites_the_bed_alarm_line_verbatim():
    from app.sim.care_plans import build_care_plan
    from app.sim.cast import build_roster

    r = next(x for x in build_roster(1337) if x.resident_id == R120)
    resident = {"code_status": r.code_status, "mobility": r.mobility, "profile": r.profile}
    current = {"tier": "CRITICAL", "score": 90.0,
               "rule": {"rule": "fall_prevention", "text": "Out of bed since 14:02 without staff"},
               "factors": [{"key": "rule:fall_prevention", "text": "Out of bed since 14:02 without staff"}]}  # fmt: skip
    card = assess.template_assessment(resident, current)
    quotes = [c["quote"] for c in card["care_plan_citations"]]
    assert any("bed alarm" in q and "assist ×1 to bathroom" in q for q in quotes)
    plan = build_care_plan(r, 1337).text()
    assert all(q in plan for q in quotes)  # never a line the care plan doesn't have


def test_an_older_card_yields_to_the_template_while_cortex_reassesses():
    from app.api.residents import _outdated

    card = {"tier": "CRITICAL", "as_of": "2026-10-17T21:56:00+00:00"}
    same = {"tier": "CRITICAL", "rule": {"since": "2026-10-17T21:55:00+00:00"}}
    newer_exit = {"tier": "CRITICAL", "rule": {"since": "2026-10-17T21:59:29+00:00"}}
    assert not _outdated(card, same)
    assert _outdated(card, newer_exit)  # a new bed exit after the card was written
    assert _outdated({"tier": "WATCH", "as_of": card["as_of"]}, {"tier": "HIGH", "rule": None})


def test_score_rise_alert_keeps_the_two_minute_debounce(monkeypatch: pytest.MonkeyPatch):
    a = assess.Assessor()
    a.triggers = True
    monkeypatch.setattr(a, "request", lambda rid, as_of, trigger: "task")
    a.last_attempt[17] = assess.time.monotonic() - 60  # assessed a minute ago
    assert a.on_alert(17, "HIGH") is None  # a score-driven rise waits out the 2-min debounce
    assert a.on_alert(17, "CRITICAL", rule=True) == "task"  # a new rule (bed exit, fall) needn't


def test_interrupted_calibration_is_cancelled(ing: Ingest):
    conn: dict = {}
    run(ing.handle({"type": "hello", "device": "phone", "resident_id": R120}, conn))
    run(ing.handle({"type": "calibrate", "action": "begin", "phase": "still"}, conn))
    assert ing.tracker(R120).calibrating == "still"
    ing.disconnect(conn)  # the tunnel blipped mid-calibration
    assert ing.tracker(R120).calibrating is None
    run(ing.handle({"type": "calibrate", "action": "begin", "phase": "shift"}, {"resident_id": R120}))
    reply = run(ing.handle({"type": "hello", "device": "phone", "resident_id": R120}, {}))  # reconnect
    assert reply["calibrating"] is None and ing.tracker(R120).calibrating is None


def test_failed_restore_backs_off_and_concurrent_restores_share_one_load(monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tiger_database_url", "postgres://test")
    i = Ingest()
    calls = []

    async def failing(rid):
        calls.append(rid)
        raise OSError("tiger down")

    monkeypatch.setattr(i, "_load_state", failing)
    run(i.restore(R120))
    run(i.restore(R120))  # within the retry window: no second query
    assert calls == [R120] and R120 not in i._restored

    async def slow(rid):
        calls.append(rid)
        await asyncio.sleep(0.01)
        return {"quiet_energy": 0.04, "calibrated": True}, ("bed_exit",)

    i2 = Ingest()
    monkeypatch.setattr(i2, "_load_state", slow)

    async def both():
        await asyncio.gather(i2.restore(R120), i2.restore(R120))

    calls.clear()
    run(both())
    assert calls == [R120]  # the second caller waited for the first restore
    assert i2.tracker(R120).in_bed is False and i2.tracker(R120).cal.calibrated
