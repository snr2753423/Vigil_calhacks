"""Bed-sensor state machine (SPEC §10) on recorded-style synthetic traces: 1-s phone summaries with
accelerometer noise, breathing micro-motion, rolling over, getting up and lying back down."""

import math

import numpy as np
import pytest

from app.devices.bed_state import (
    BURST_FLOOR,
    EXIT_STILL_S,
    QUIET_FLOOR,
    BedTracker,
    Calibration,
    Summary,
    angle_deg,
)

T0 = 1_700_000_000.0
G = 9.81


def tilt(deg: float) -> tuple[float, float, float]:
    """Gravity with the phone tipped `deg` degrees about its x axis (flat on its back = 0)."""
    r = math.radians(deg)
    return 0.0, G * math.sin(r), G * math.cos(r)


class Trace:
    """Builds 1-s summaries like the /bed page sends: noise floor, breathing, bursts, tilt."""

    def __init__(self, seed: int = 7, empty_deg: float = 0.0, occupied_deg: float = 3.0, gravity: bool = True):
        self.rng = np.random.default_rng(seed)
        self.t = T0
        self.empty_deg, self.occupied_deg, self.gravity = empty_deg, occupied_deg, gravity
        self.out: list[Summary] = []

    def _add(self, energy: float, jolt: float, deg: float) -> None:
        g = None
        if self.gravity:
            x, y, z = tilt(deg + self.rng.normal(0, 0.03))
            g = (x, y, z)
        self.out.append(Summary(self.t, max(energy, 0.0), max(jolt, energy), g))
        self.t += 1

    def empty(self, s: int) -> "Trace":
        for _ in range(s):
            e = abs(self.rng.normal(0.006, 0.002))
            self._add(e, e * 2.5, self.empty_deg)
        return self

    def lying(self, s: int, deg: float | None = None) -> "Trace":
        """Lying still: breathing lifts the phone a little above the noise floor."""
        for _ in range(s):
            e = abs(self.rng.normal(0.018, 0.004))
            self._add(e, e * 2.5, self.occupied_deg if deg is None else deg)
        return self

    def shift(self, s: int, peak: float = 2.0, deg: float | None = None) -> "Trace":
        """Rolling over / fidgeting in bed."""
        for i in range(s):
            self._add(
                0.35 + self.rng.normal(0, 0.05),
                peak * (1.0 if i == s // 2 else 0.6),
                self.occupied_deg if deg is None else deg,
            )
        return self

    def get_up(self) -> "Trace":
        """Sitting up and standing: two big jolts while the weight comes off the bed."""
        self._add(0.5, 3.0, self.occupied_deg)
        self._add(0.6, 3.5, (self.occupied_deg + self.empty_deg) / 2)
        return self

    def lie_down(self) -> "Trace":
        self._add(0.6, 3.2, (self.occupied_deg + self.empty_deg) / 2)
        self._add(0.4, 2.0, self.occupied_deg)
        return self


def calibrate(tracker: BedTracker, trace: Trace, *, empty: bool = True) -> Calibration:
    if empty:
        tracker.begin_calibration("empty")
        for s in trace.empty(5).out[-5:]:
            tracker.step(s)
    tracker.begin_calibration("still")
    for s in trace.lying(10).out[-10:]:
        tracker.step(s)
    tracker.begin_calibration("shift")
    for s in trace.shift(5).out[-5:]:
        tracker.step(s)
    cal, _ = tracker.finish_calibration(trace.t)
    return cal


def run(tracker: BedTracker, summaries: list[Summary]):
    """Step through and return (transitions, readings)."""
    readings = [tracker.step(s) for s in summaries]
    return [r.transition for r in readings if r.transition], readings


# --------------------------------------------------------------------------- calibration


def test_angle_is_accurate_for_tiny_tilts():
    assert angle_deg(tilt(0), tilt(0.05)) == pytest.approx(0.05, abs=1e-6)
    assert angle_deg(tilt(0), tilt(30)) == pytest.approx(30, abs=1e-9)
    assert angle_deg((0, 0, 0), tilt(10)) == 0.0


def test_calibration_thresholds_from_samples():
    tr, tracker = Trace(), BedTracker()
    cal = calibrate(tracker, tr)
    # quiet = 2× the lying-still p95, burst = half the shift's 2.0 peak
    assert QUIET_FLOOR <= cal.quiet_energy < 0.1
    assert cal.burst_jolt == pytest.approx(1.0, abs=0.01)
    assert cal.tilt_sep_deg == pytest.approx(3.0, abs=0.1)
    assert cal.tilt_ok and cal.mode == "tilt"
    assert cal.energy_ok  # breathing (~0.018) is clearly above the empty bed (~0.006)
    assert cal.warnings == ()


def test_calibration_needs_lying_still():
    with pytest.raises(ValueError):
        Calibration.from_samples(still=[Summary(T0, 0.01, 0.02)])


def test_calibration_without_empty_step_falls_back_to_the_spec_rule():
    cal = calibrate(BedTracker(), Trace(), empty=False)
    assert cal.mode == "burst"
    assert any("10 s" in w for w in cal.warnings)


def test_weak_shift_warns_and_keeps_floor():
    still = Trace().lying(10).out
    shift = [Summary(T0 + 20 + i, 0.03, 0.1) for i in range(5)]
    cal = Calibration.from_samples(still, shift)
    assert cal.burst_jolt >= BURST_FLOOR
    assert any("barely registered" in w for w in cal.warnings)


def test_calibration_round_trips_through_dict():
    cal = calibrate(BedTracker(), Trace())
    assert Calibration.from_dict(cal.to_dict()) == cal


def test_finishing_calibration_puts_the_presenter_back_in_bed():
    tracker = BedTracker(in_bed=False)
    tr = Trace()
    tracker.begin_calibration("still")
    for s in tr.lying(10).out:
        r = tracker.step(s)
        assert r.in_bed is None and r.calibrating == "still"  # nothing is decided while calibrating
    _, t = tracker.finish_calibration(tr.t)
    assert tracker.in_bed and t is not None and t.kind == "bed_return" and t.reason == "calibration"


# --------------------------------------------------------------------------- tilt mode (phone under a pillow)


def test_tilt_mode_bed_exit_within_seconds_and_return():
    tr, tracker = Trace(), BedTracker()
    calibrate(tracker, tr)
    start = len(tr.out)
    tr.lying(30).get_up().empty(15)
    up_ts = tr.out[start + 30].ts
    transitions, readings = run(tracker, tr.out[start:])
    assert [t.kind for t in transitions] == ["bed_exit"]
    exit_reading = next(r for r in readings if r.transition)
    assert exit_reading.ts - up_ts <= 3  # confirmed ~2 s after the weight came off
    assert transitions[0].reason == "tilt"
    assert readings[-1].in_bed is False and readings[-1].bed_motion == 0.0

    start = len(tr.out)
    tr.lie_down().lying(10)
    transitions, readings = run(tracker, tr.out[start:])
    assert [t.kind for t in transitions] == ["bed_return"]
    assert readings[-1].in_bed is True


def test_tilt_mode_rolling_over_is_not_an_exit():
    tr, tracker = Trace(), BedTracker()
    calibrate(tracker, tr)
    start = len(tr.out)
    # roll over to a new position (a different, still-occupied tilt), then lie still for a long time
    tr.lying(20).shift(4, peak=2.5, deg=4.5).lying(60, deg=4.5).shift(3, deg=2.2).lying(60, deg=2.2)
    transitions, readings = run(tracker, tr.out[start:])
    assert transitions == []
    assert all(r.in_bed for r in readings)


def test_tilt_mode_without_gravity_uses_the_energy_rule():
    tr, tracker = Trace(), BedTracker()
    calibrate(tracker, tr)
    start = len(tr.out)
    tr.gravity = False  # e.g. the browser stopped sending accelerationIncludingGravity
    tr.lying(20).get_up().empty(10)
    transitions, _ = run(tracker, tr.out[start:])
    assert [(t.kind, t.reason) for t in transitions] == [("bed_exit", "energy")]


# --------------------------------------------------------------------------- energy mode (no usable tilt)


def test_energy_mode_exit_after_burst_and_empty_quiet():
    tr = Trace(occupied_deg=0.02)  # a thick mattress: lying down doesn't tip the phone
    tracker = BedTracker()
    cal = calibrate(tracker, tr)
    assert cal.mode == "energy"
    start = len(tr.out)
    tr.lying(20).get_up().empty(10)
    up_ts = tr.out[start + 20].ts
    transitions, readings = run(tracker, tr.out[start:])
    assert [(t.kind, t.reason) for t in transitions] == [("bed_exit", "energy")]
    assert transitions[0].ts == up_ts + 1  # stamped at the burst (the second jolt), not at confirmation
    confirmed = next(r for r in readings if r.transition)
    assert confirmed.ts - up_ts <= 6

    start = len(tr.out)
    tr.lie_down().lying(5)
    transitions, _ = run(tracker, tr.out[start:])
    assert [t.kind for t in transitions] == ["bed_return"]


def test_energy_mode_lying_still_after_rolling_over_is_not_an_exit():
    tr = Trace(occupied_deg=0.02)
    tracker = BedTracker()
    calibrate(tracker, tr)
    start = len(tr.out)
    tr.lying(10).shift(3).lying(60)
    transitions, _ = run(tracker, tr.out[start:])
    assert transitions == []


# --------------------------------------------------------------------------- burst mode (SPEC rule)


def test_burst_mode_needs_ten_seconds_of_stillness():
    tr = Trace(gravity=False)
    tracker = BedTracker()
    cal = calibrate(tracker, tr, empty=False)
    assert cal.mode == "burst"
    start = len(tr.out)
    tr.lying(10).get_up().empty(EXIT_STILL_S - 2)
    transitions, _ = run(tracker, tr.out[start:])
    assert transitions == []  # not yet: still under 10 s of stillness
    more = len(tr.out)
    tr.empty(3)
    transitions, _ = run(tracker, tr.out[more:])
    assert [(t.kind, t.reason) for t in transitions] == [("bed_exit", "burst")]
    assert transitions[0].ts == tr.out[start + 11].ts  # the getting-up burst

    # another burst = back in bed
    start = len(tr.out)
    tr.empty(5).lie_down()
    transitions, _ = run(tracker, tr.out[start:])
    assert [t.kind for t in transitions] == ["bed_return"]


def test_burst_mode_restlessness_without_stillness_stays_in_bed():
    tr = Trace(gravity=False)
    tracker = BedTracker()
    calibrate(tracker, tr, empty=False)
    start = len(tr.out)
    for _ in range(10):  # fidgeting every few seconds: never 10 s still after a burst
        tr.shift(2).lying(4)
    transitions, readings = run(tracker, tr.out[start:])
    assert transitions == []
    assert readings[-1].level >= 2  # restless


def test_uncalibrated_tracker_uses_defaults():
    tracker = BedTracker()
    assert not tracker.cal.calibrated and tracker.cal.mode == "burst"
    tr = Trace(gravity=False)
    start = len(tr.out)
    tr.lying(5).get_up().empty(12)
    transitions, _ = run(tracker, tr.out[start:])
    assert [t.kind for t in transitions] == ["bed_exit"]


# --------------------------------------------------------------------------- outputs


def test_restlessness_levels_and_bed_motion_scale():
    tr, tracker = Trace(), BedTracker()
    calibrate(tracker, tr)
    start = len(tr.out)
    tr.lying(60)
    _, readings = run(tracker, tr.out[start:])
    assert readings[-1].level == 0 and readings[-1].state == "in bed · still"
    assert 0 < readings[-1].bed_motion < 1  # lying still reads below 1 on the 0–10 scale
    start = len(tr.out)
    for _ in range(15):
        tr.shift(2, peak=0.8).lying(2)
    _, readings = run(tracker, tr.out[start:])
    assert readings[-1].level >= 2
    assert max(r.bed_motion for r in readings) > 3


def test_force_sets_state_once():
    tracker = BedTracker()
    t = tracker.force(False, T0)
    assert t is not None and t.kind == "bed_exit" and t.reason == "admin"
    assert tracker.force(False, T0 + 1) is None  # already out
    assert tracker.force(True, T0 + 2).kind == "bed_return"


def test_prep_pauses_are_not_recorded():
    tracker = BedTracker()
    tr = Trace()
    tracker.begin_calibration("prep")
    for s in tr.shift(6).out:  # lying down: lots of motion that must not inflate the thresholds
        assert tracker.step(s).calibrating == "prep"
    tracker.begin_calibration("still")
    for s in tr.lying(10).out[-10:]:
        tracker.step(s)
    cal, _ = tracker.finish_calibration(tr.t)
    assert cal.quiet_energy < 0.1
    with pytest.raises(ValueError):
        tracker.begin_calibration("nap")
