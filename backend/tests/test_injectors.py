"""Scenario injectors (SPEC §8): each changes only ticks from the injection on, and drives the rules."""

from datetime import date

import numpy as np
import pytest

from app.config import get_settings
from app.sim import cast, injectors
from app.sim import facility as fac
from app.sim.live_loop import LiveLoop
from app.sim.physiology import ResidentSim
from app.state import sim_state

SEED = 1337
TZ = fac.tzinfo("America/Los_Angeles")
NIGHT_DATE = date(2026, 10, 10)
INJECT = int(fac.local_epoch(NIGHT_DATE, 26, TZ))  # 02:00, asleep in bed
RID = 5


@pytest.fixture
def sim() -> ResidentSim:
    r = next(x for x in cast.build_roster(SEED) if x.resident_id == RID)
    return ResidentSim(cast.resident_params(SEED, r), SEED, TZ)


def run(sim: ResidentSim, scenario=None, before_s=600, after_s=1800):
    """Simulate up to the injection, inject, then continue — like the live loop does."""
    t0 = INJECT - before_s
    pre = sim.step(t0, before_s // fac.TICK_S)
    if scenario is not None:
        sim.scenarios = [*sim.scenarios, scenario]
    post = sim.step(INJECT, after_s // fac.TICK_S)
    return pre, post


def secs(post) -> np.ndarray:
    return post.cols["ts"] // 1_000_000 - INJECT


def test_fall_impact_then_still_on_the_floor_out_of_bed(sim) -> None:
    pre, post = run(sim, injectors.make("fall", RID, 10, start=INJECT))
    assert pre.cols["in_bed"][-1] == 1 and np.all(np.isnan(pre.cols["impact"]))
    t, c = secs(post), post.cols
    assert c["impact"][0] >= 4.0  # spike on the first tick
    assert np.all(np.isnan(c["impact"][1:]))
    on_floor = (t > 0) & (t < 600)  # found (helped up) at +600 s
    assert np.all(c["motion"][on_floor] < 2.0)  # the rule's "no motion"
    assert np.all(c["in_bed"][t < 840] == 0)
    assert np.all(c["worn"][t < 840] == 1)
    assert [e.type for e in post.events if e.type.startswith("bed_")][:1] == ["bed_exit"]


def test_remove_band_masks_the_wristband(sim) -> None:
    _, post = run(sim, injectors.make("remove_band", RID, 20, start=INJECT))
    t, c = secs(post), post.cols
    off = t < 1200
    assert np.all(c["worn"][off] == 0) and np.all(np.isnan(c["hr"][off]))
    assert np.all(c["worn"][~off] == 1) and not np.any(np.isnan(c["hr"][~off]))


def test_bed_exit_leaves_bed_after_one_tick(sim) -> None:
    _, post = run(sim, injectors.make("bed_exit", RID, 5, start=INJECT))
    t, c = secs(post), post.cols
    assert c["in_bed"][0] == 1 and np.all(c["in_bed"][(t >= 5) & (t < 300)] == 0)
    exits = [e for e in post.events if e.type == "bed_exit"]
    assert exits and exits[0].ts == INJECT + 5


def test_sepsis_drift_ramps_hr_over_90_min(sim) -> None:
    r = next(x for x in cast.build_roster(SEED) if x.resident_id == RID)
    control = ResidentSim(cast.resident_params(SEED, r), SEED, TZ)
    _, base = run(control, after_s=7200)
    _, post = run(sim, injectors.make("sepsis_drift", RID, start=INJECT), after_s=7200)
    t = secs(post)
    late = t >= 5400
    diff = np.nanmean(post.cols["hr"][late]) - np.nanmean(base.cols["hr"][late])
    assert 20 < diff < 30
    assert np.nanmean(post.cols["rr"][late]) - np.nanmean(base.cols["rr"][late]) > 4


def test_unknown_injector() -> None:
    with pytest.raises(injectors.InjectError):
        injectors.make("earthquake", RID)


def test_live_loop_inject_respects_the_live_resident(monkeypatch) -> None:
    monkeypatch.setenv("SIM_LIVE_ENABLED", "false")
    get_settings.cache_clear()
    loop = LiveLoop(get_settings())
    loop._build(loop.seed, NIGHT_DATE)
    monkeypatch.setattr(sim_state, "simulated_band", False)
    with pytest.raises(injectors.InjectError) as err:
        loop.inject(injectors.make("fall", fac.LIVE_RESIDENT_ID))
    assert err.value.status == 409
    loop.inject(injectors.make("fall", RID))
    assert [i["kind"] for i in loop.injections()] == ["fall"]
    get_settings.cache_clear()
