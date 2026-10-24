"""Live loop pieces that do not need a database."""

from datetime import date

import pytest

from app.config import get_settings
from app.sim import facility as fac
from app.sim.live_loop import LiveLoop, current_tick
from app.state import sim_state


def test_current_tick_never_in_the_future() -> None:
    assert current_tick(1_700_000_004.9) == 1_700_000_000
    assert current_tick(1_700_000_005.0) == 1_700_000_005


@pytest.fixture
def loop(monkeypatch: pytest.MonkeyPatch) -> LiveLoop:
    monkeypatch.setenv("SIM_LIVE_ENABLED", "false")
    get_settings.cache_clear()
    live = LiveLoop(get_settings())
    live._build(live.seed, date(2026, 10, 15))
    yield live
    get_settings.cache_clear()


def test_r120_streams_only_with_the_simulated_band(loop: LiveLoop, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sim_state, "simulated_band", False)
    ids = loop.active_ids()
    assert len(ids) == 119 and fac.LIVE_RESIDENT_ID not in ids
    monkeypatch.setattr(sim_state, "simulated_band", True)
    assert fac.LIVE_RESIDENT_ID in loop.active_ids()


def test_one_tick_for_every_active_resident(loop: LiveLoop) -> None:
    ids = loop.active_ids()
    batches = loop._generate(ids, current_tick() - 60, 1)
    assert [int(b.cols["resident_id"][0]) for b in batches] == ids
    assert all(b.rows == 1 for b in batches)


def test_rebuild_only_when_the_seed_or_scripted_night_changes(loop: LiveLoop) -> None:
    sims, names = loop.sims, [r.name for r in loop.roster]
    loop._build(loop.seed, date(2026, 10, 15))
    assert loop.sims is sims
    loop._build(loop.seed, date(2026, 10, 16))
    assert loop.sims is not sims and loop.night == date(2026, 10, 16)
    loop._build(loop.seed + 1, date(2026, 10, 16))  # history seeded with `--seed`: follow it
    assert [r.name for r in loop.roster] != names


def test_staff_events_are_never_ahead_of_the_tick(loop: LiveLoop) -> None:
    """Live steps emit an event only once its tick has happened: (t0 - 5 s, t0] for a 1-tick step."""
    # 3 h, not 1: rounds come every 2 h (±20 min), so a 1-h window right after one held no events at all for these
    # residents (the test failed depending on the time of day it ran).
    t0, ids, seen = current_tick() - 3 * 3600, loop.active_ids()[:12], 0
    for i in range(3 * 720):
        tick = t0 + 5 * i
        for batch in loop._generate(ids, tick, 1):
            assert all(tick - 5 < e.ts <= tick for e in batch.events)
            seen += len(batch.events)
    assert seen  # 3 h always hold a round's checks for some of these residents
