"""WebSocket hub for `/ws/live` (server → client: queue, readings, alerts, insights, sim state).

Phase 1: connection registry plus a heartbeat broadcast every `heartbeat_interval_s`.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.state import sim_state

log = logging.getLogger(__name__)

SEND_TIMEOUT_S = 1.0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send to every client concurrently; drop clients that error or stall."""
        clients = list(self._clients)
        if not clients:
            return
        results = await asyncio.gather(
            *(asyncio.wait_for(ws.send_json(message), SEND_TIMEOUT_S) for ws in clients),
            return_exceptions=True,
        )
        for ws, result in zip(clients, results, strict=True):
            if isinstance(result, BaseException):
                await self._drop(ws)

    async def _drop(self, ws: WebSocket) -> None:
        """Forget the client and close its socket so the browser notices and reconnects."""
        self.disconnect(ws)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(code=1011), SEND_TIMEOUT_S)


hub = Hub()


def heartbeat_message(seq: int) -> dict[str, Any]:
    return {
        "type": "heartbeat",
        "seq": seq,
        "ts": utc_now_iso(),
        "clients": hub.client_count,
        "sim": sim_state.to_dict(),
    }


async def heartbeat_loop(interval_s: float) -> None:
    seq = 0
    while True:
        seq += 1
        try:
            await hub.broadcast(heartbeat_message(seq))
        except Exception:  # never let the heartbeat task die
            log.exception("heartbeat broadcast failed")
        await asyncio.sleep(interval_s)


router = APIRouter()


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json(
            {
                "type": "hello",
                "ts": utc_now_iso(),
                "heartbeat_interval_s": get_settings().heartbeat_interval_s,
                "sim": sim_state.to_dict(),
            }
        )
        while True:  # client → server messages are ignored for now
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):  # RuntimeError: the hub already closed this socket
        pass
    finally:
        hub.disconnect(ws)
