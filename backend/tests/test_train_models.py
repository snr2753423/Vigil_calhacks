"""`make train --no-train`: which models already in Snowflake may be registered for the current seed."""

import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import BACKEND_DIR

sys.path.insert(0, str(BACKEND_DIR / "scripts"))
import train_models  # noqa: E402  (a script, importable once scripts/ is on the path)

TRAIN_TO = datetime(2026, 10, 16, 19, 0, tzinfo=UTC)
SEEDED = datetime(2026, 10, 17, 17, 25, tzinfo=UTC)
WINDOW_WRITTEN = datetime(2026, 10, 17, 17, 29)  # naive UTC, as Snowflake returns SYSDATE()
LA = ZoneInfo("America/Los_Angeles")
NAMES = [name for name, _ in train_models.anomaly.MODELS.values()]


def stub(monkeypatch, *, train_to=TRAIN_TO, tz="America/Los_Angeles", created=None, names=NAMES) -> None:
    created = created or datetime(2026, 10, 17, 11, 30, tzinfo=LA)  # 18:30 UTC, after both
    window = [(datetime(2026, 10, 7, 17, 15), train_to.replace(tzinfo=None), tz, WINDOW_WRITTEN)]
    monkeypatch.setattr(train_models.sf, "query", lambda conn, sql, params=None, **kw: window)
    monkeypatch.setattr(
        train_models.sf,
        "query_dicts",
        lambda conn, sql, params=None, **kw: [{"name": n, "created_on": created} for n in names],
    )


def test_registers_models_trained_for_this_seed(monkeypatch) -> None:
    stub(monkeypatch)
    train_from, models = train_models.existing_models(None, TRAIN_TO, SEEDED)
    assert train_from == datetime(2026, 10, 7, 17, 15, tzinfo=UTC)
    assert models["motion"] == {"model": "ML.AD_MOTION", "seconds": None} and len(models) == 5


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"train_to": TRAIN_TO - timedelta(days=1)}, "window"),
        ({"tz": None}, "time zone"),
        ({"names": NAMES[:-1]}, "missing"),
        ({"created": datetime(2026, 10, 17, 10, 0, tzinfo=LA)}, "older"),  # 17:00 UTC: before the seed loaded
        ({"created": datetime(2026, 10, 17, 10, 27, tzinfo=LA)}, "older"),  # after the seed, before the window
    ],
)
def test_refuses_stale_or_incomplete_models(monkeypatch, kw, why) -> None:
    stub(monkeypatch, **kw)
    with pytest.raises(SystemExit, match=why):
        train_models.existing_models(None, TRAIN_TO, SEEDED)
