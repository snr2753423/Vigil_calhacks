import pytest
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


def test_ws_client_is_closed_after_a_failed_send(client: TestClient) -> None:
    """A client the hub drops must be closed (so the browser reconnects), not left open and silent."""
    from starlette.websockets import WebSocketDisconnect

    from app.ws.hub import hub

    with client.websocket_connect("/ws/live") as ws:
        ws.receive_json()  # hello
        ws.receive_json()  # first heartbeat
        (server_ws,) = list(hub._clients)

        async def failing_send(*_args, **_kwargs):
            raise TimeoutError("simulated stall")

        server_ws.send_json = failing_send
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_json()
        assert closed.value.code == 1011
        assert hub.client_count == 0
