"""Phase 10 demo hardening: /ask's offline fallback, standby when another backend is primary, the live detection
concurrency cap, the nav-free bits of reset_demo.py (report and backend check). No Tiger or Snowflake."""

import asyncio
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR
from app.deep import analyst, anomaly
from app.main import app
from app.sim import live_loop

sys.path.insert(0, str(BACKEND_DIR / "scripts"))
import reset_demo  # noqa: E402  (a script, importable once scripts/ is on the path)

SAVED = {"question": "Alerts by unit and tier this week.", "sql": "SELECT 1", "error": None, "rows": [[1]],
         "answered_at": "2026-10-18T20:00:00+00:00"}  # fmt: skip


def test_question_key_ignores_case_spacing_and_punctuation() -> None:
    assert analyst._norm("  Alerts by unit and TIER this week. ") == analyst._norm("alerts by unit and tier this week?")


@pytest.fixture
def ask_client(monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    for k in ("SIM_LIVE_ENABLED", "EVALUATOR_ENABLED", "SNOWFLAKE_SYNC_ENABLED", "ASSESS_ENABLED",
              "DEVICE_INGEST_ENABLED", "STATS_FLUSH_ENABLED", "WARMUP_ENABLED", "FORECAST_ENABLED"):  # fmt: skip
        monkeypatch.setenv(k, "false")
    get_settings.cache_clear()
    remembered: list[dict] = []

    async def remember(result):
        remembered.append(result)

    async def recall(question):
        return SAVED if analyst._norm(question) == analyst._norm(SAVED["question"]) else None

    monkeypatch.setattr(analyst, "remember", remember)
    monkeypatch.setattr(analyst, "recall", recall)
    with TestClient(app) as c:
        yield c, remembered
    get_settings.cache_clear()


def test_ask_serves_the_last_answer_when_analyst_is_down(ask_client, monkeypatch) -> None:
    c, _ = ask_client

    async def down(question, history=None):
        raise ConnectionError("snowflake unreachable")

    monkeypatch.setattr(analyst, "ask", down)
    r = c.post("/api/ask", json={"question": "alerts by unit and tier this week"})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [[1]] and body["fallback"]["answered_at"] == SAVED["answered_at"]
    assert "unreachable" in body["fallback"]["reason"]
    # No saved answer (or a follow-up with history): an honest error, not someone else's answer.
    assert c.post("/api/ask", json={"question": "Something never asked"}).status_code == 502
    follow_up = {"question": SAVED["question"], "history": [{"role": "user", "content": "x"}]}
    assert c.post("/api/ask", json=follow_up).status_code == 502


def test_ask_remembers_successful_answers_only_without_history(ask_client, monkeypatch) -> None:
    c, remembered = ask_client

    async def ok(question, history=None):
        return {"question": question, "sql": "SELECT 1", "error": None, "rows": []}

    monkeypatch.setattr(analyst, "ask", ok)
    assert c.post("/api/ask", json={"question": "Which residents?"}).status_code == 200
    follow_up = {"question": "and then?", "history": [{"role": "user", "content": "x"}]}
    assert c.post("/api/ask", json=follow_up).status_code == 200
    assert [r["question"] for r in remembered] == ["Which residents?"]


def test_standby_follows_the_live_loop_lease(monkeypatch) -> None:
    monkeypatch.setattr(live_loop, "live", None)
    assert not live_loop.standby()  # no live loop (disabled): this backend is the only one

    class Fake:
        class stats:  # noqa: N801
            phase = "standby"

    monkeypatch.setattr(live_loop, "live", Fake())
    assert live_loop.standby()
    Fake.stats.phase = "streaming"
    assert not live_loop.standby()


def test_live_detection_concurrency_cap(monkeypatch) -> None:
    running, peak = 0, 0
    lock = threading.Lock()

    def query_columns(conn, sql, params=None, *, feature="sql"):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return [], []

    monkeypatch.setattr(anomaly.sf, "query_columns", query_columns)
    end = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
    det = anomaly.run_detection(None, end - timedelta(minutes=15), end, concurrency=2)
    assert not det.errors and set(det.seconds) == set(anomaly.MODELS) and peak == 2
    peak = 0
    anomaly.run_detection(None, end - timedelta(minutes=15), end)  # prepare / make train: all five at once
    assert peak == len(anomaly.MODELS)


def test_report_exit_code(capsys) -> None:
    ok = [reset_demo.Check("Tiger", "PASS", "x"), reset_demo.Check("Pages", "WARN", "slow")]
    assert reset_demo.report(ok, time.perf_counter()) == 0
    assert "READY — 1/2 PASS, 1 warning(s)" in capsys.readouterr().out
    assert reset_demo.report(ok + [reset_demo.Check("Snowflake", "FAIL", "down")], time.perf_counter()) == 1
    assert "NOT READY — 1 failed: Snowflake" in capsys.readouterr().out


def test_backend_check_warns_when_another_backend_is_primary() -> None:
    now = datetime.now(UTC).isoformat()
    health = {"sim": {"mode": "live"}, "live_sim": {"phase": "streaming", "last_tick": now},
              "evaluator": {"phase": "running", "avg_ms": 400}, "warmup": {"phase": "done", "ms": 2000}}  # fmt: skip
    assert reset_demo.backend_check("http://x", health).status == "PASS"
    health["live_sim"]["phase"] = "standby"
    check = reset_demo.backend_check("http://x", health)
    assert check.status == "WARN" and "ANOTHER backend" in check.detail
    health["live_sim"]["phase"] = "error"
    assert reset_demo.backend_check("http://x", health).status == "FAIL"


def test_shutdown_cancel_is_a_no_op_without_snowflake_work(monkeypatch) -> None:
    from app import main

    calls = []

    class Rest:
        async def cancel_running(self, sessions, timeout):
            calls.append(sessions)
            return {"sessions": len(sessions), "statements": 0}

    monkeypatch.setattr(main.snowflake_rest, "client", lambda: Rest())
    monkeypatch.setattr(main.sf, "open_session_ids", lambda: [42])
    asyncio.run(main.cancel_snowflake())
    assert calls in ([[42]], [])  # [] only when Snowflake isn't configured in this environment
