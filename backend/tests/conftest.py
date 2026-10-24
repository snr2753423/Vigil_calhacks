import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HEARTBEAT_INTERVAL_S", "0.05")
    monkeypatch.setenv("SIM_LIVE_ENABLED", "false")  # tests never stream into the real Tiger service
    get_settings.cache_clear()
    with TestClient(app) as c:  # context manager runs the lifespan (heartbeat task)
        yield c
    get_settings.cache_clear()
