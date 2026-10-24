"""API pieces that don't need Tiger: as_of parsing, simulator controls, the queue payloads, the demo clock."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import parse_ts, tiger_conn
from app.db import tiger
from app.main import app
from app.scoring import evaluator as ev
from app.scoring import priority, rules
from app.scoring.rules import NightClock
from app.state import sim_state
from tests.scoring_helpers import NIGHT, TZ, baselines, clock, features, profile


def test_parse_ts() -> None:
    assert parse_ts(None) is None
    assert parse_ts("2026-10-16T02:14:00-07:00") == datetime(2026, 10, 16, 9, 14, tzinfo=UTC)
    assert parse_ts("2026-10-16T09:14:00Z") == datetime(2026, 10, 16, 9, 14, tzinfo=UTC)
    assert parse_ts("2026-10-16T09:14:00") == datetime(2026, 10, 16, 9, 14, tzinfo=UTC)  # naive = UTC
    assert parse_ts("2026-10-16T02:14:00 07:00") == datetime(2026, 10, 15, 19, 14, tzinfo=UTC)  # '+' decoded as ' '
    with pytest.raises(HTTPException):
        parse_ts("yesterday")
    with pytest.raises(HTTPException):
        parse_ts((datetime.now(UTC) + timedelta(minutes=1)).isoformat())


@pytest.fixture
def clean_sim_state():
    saved = sim_state.to_dict()
    yield
    for k, v in saved.items():
        setattr(sim_state, k, v)


def test_force_night_and_pause(client: TestClient, clean_sim_state) -> None:
    r = client.post("/api/sim/force_night", json={"enabled": True, "display_clock_offset_s": 3600})
    assert r.status_code == 200
    body = r.json()
    assert body["force_night"] is True and body["force_night_since"] and body["display_clock_offset_s"] == 3600
    assert client.post("/api/sim/force_night", json={"enabled": False}).json()["force_night_since"] is None
    assert client.post("/api/sim/pause").json()["paused"] is True
    assert client.post("/api/sim/start").json()["paused"] is False


def test_inject_needs_the_live_simulator(client: TestClient) -> None:
    r = client.post("/api/sim/inject", json={"kind": "fall", "resident_id": 5})
    assert r.status_code == 409  # tests run with SIM_LIVE_ENABLED=false
    assert client.post("/api/sim/inject", json={"kind": "fall", "resident_id": 500}).status_code == 422


# --------------------------------------------------------------------------- queue (no Tiger needed)


def _queue(as_of: datetime, *, score: float = 40.0) -> ev.Queue:
    p, f = profile(), features(as_of)
    res = priority.compute(p, f, baselines(), clock())
    dec = ev.AlertGate(persistence=1).update(7, score, rules=[], last_check_ts=None, as_of=as_of)
    return ev.Queue(as_of, [ev.QueueItem(p, f, res, dec, clock())])


@pytest.fixture
def fake_live(client: TestClient, clean_sim_state):
    """A live evaluator whose latest queue is a minute old, and no Tiger connection."""
    live = ev.Evaluator()
    live.latest = _queue(datetime.now(UTC) - timedelta(minutes=1))
    app.dependency_overrides[tiger_conn] = lambda: None
    saved, ev.evaluator = ev.evaluator, live
    yield live
    ev.evaluator = saved
    app.dependency_overrides.pop(tiger_conn, None)


def test_paused_queue_stays_frozen(client: TestClient, fake_live) -> None:
    sim_state.paused = True
    body = client.get("/api/queue").json()
    assert body["as_of"] == fake_live.latest.as_of.isoformat() and body["items"][0]["resident_id"] == 7


def test_ws_queue_is_slim_and_rest_has_detail() -> None:
    q = _queue(NIGHT)
    slim, full = q.to_dict(detail=False)["items"][0], q.to_dict()["items"][0]
    assert {"components", "news2", "concern", "score_tier"} <= full.keys()
    assert not {"components", "news2", "concern", "score_tier"} & slim.keys()
    assert {"tier", "score", "factors", "rule", "hr_spark", "minutes_since_check"} <= slim.keys()


def test_queue_signature_ignores_vitals_but_not_tier() -> None:
    a, b = _queue(NIGHT), _queue(NIGHT)
    b.items[0].features.hr_5m = 71.0  # a new HR reading: same card
    assert a.signature() == b.signature() == _queue(NIGHT, score=44.0).signature()  # score drift within WATCH
    assert _queue(NIGHT, score=70.0).signature() != a.signature()  # WATCH → HIGH is pushed at once


def test_historical_evaluation_ignores_the_demo_clock(clean_sim_state) -> None:
    sim_state.force_night, sim_state.display_clock_offset_s = True, 12 * 3600
    sim_state.force_night_since = None
    live, past = ev.night_clock(TZ), ev.night_clock(TZ, live=False)
    assert live.offset_s == 12 * 3600 and live.force_night
    assert past == NightClock(TZ)
    assert past.is_night(NIGHT) and past.night_start(NIGHT) is not None  # 02:14 stays night
    assert live.hhmm(NIGHT) == "14:14" and past.hhmm(NIGHT) == "02:14"  # the demo offset shifts only live


def test_impact_query_uses_the_rule_threshold() -> None:
    assert f"impact >= {float(rules.FALL_IMPACT_G)}" in tiger.SCORING_QUERIES["impact"]
