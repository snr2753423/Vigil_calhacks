"""GET /api/insights/{id}?kind=forecast recomputes crossings and the callout from the stored points."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import tiger_conn
from app.db import tiger
from app.main import app

AS_OF = datetime(2026, 10, 17, 8, 22, tzinfo=UTC)


def test_forecast_items_carry_the_current_callout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 10, 17, 8, 15, tzinfo=UTC)
    pts = [[(start + timedelta(minutes=15 * i)).isoformat(), 21.0 + i, 20.0 + i, 22.0 + i] for i in range(6)]
    payload = {"as_of": AS_OF.isoformat(), "copd": False, "callout": "stale",
               "metrics": {"rr": {"points": pts, "last_value": 20.3, "crossing": None}}}  # fmt: skip

    async def insights_range(conn, rid, kind, frm, to):
        return [{"as_of": AS_OF, "payload": payload, "created_at": AS_OF}]

    async def fetchone(conn, sql, params=None):
        return (["beta_blocker"],)

    monkeypatch.setattr(tiger, "insights_range", insights_range)
    monkeypatch.setattr(tiger, "fetchone", fetchone)
    app.dependency_overrides[tiger_conn] = lambda: None
    try:
        r = client.get("/api/insights/42?kind=forecast&from=2026-10-17T06:30:00Z&to=2026-10-17T08:30:00Z")
    finally:
        app.dependency_overrides.pop(tiger_conn, None)
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["as_of"] == AS_OF.isoformat()
    assert item["callout"] == "RR forecast to cross 25 by ~02:30" and item["p_trend"] == 1.0
