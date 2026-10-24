from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sim"]["mode"] == "live"
    assert set(body["configured"]) == {"tiger", "snowflake"}


def test_ws_live_sends_hello_then_heartbeats(client: TestClient) -> None:
    with client.websocket_connect("/ws/live") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        first, second = ws.receive_json(), ws.receive_json()
        assert first["type"] == second["type"] == "heartbeat"
        assert second["seq"] == first["seq"] + 1
        assert first["clients"] >= 1
        assert first["ts"].endswith("Z")
