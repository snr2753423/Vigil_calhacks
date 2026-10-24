"""Scenario injectors for the live simulator (SPEC §8): the admin buttons and the hardware fallback.

Each injector is a time-boxed `Scenario` added to one resident's live `ResidentSim`. It starts at the
injection time (it only changes ticks that have not been written yet, never history), and the resident
returns to their normal plan when it ends. Bed exits/returns are emitted by the simulator itself from the
changed `in_bed` signal, exactly as for scripted events.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from app.sim import facility as fac
from app.sim.physiology import MOTION_PER_ACTIVITY, Scenario, Signals

KINDS = ("fall", "sepsis_drift", "remove_band", "bed_exit")
DEFAULT_MINUTES = {"fall": 10.0, "sepsis_drift": 360.0, "remove_band": 30.0, "bed_exit": 8.0}
WALK_MOTION = 0.7 * MOTION_PER_ACTIVITY


class Injected(Scenario):
    kind = "injected"

    def __init__(self, resident_id: int, start: float, minutes: float):
        self.resident_id = resident_id
        self.start = float(start)
        self.minutes = float(minutes)
        self.end = self.start + self.minutes * 60

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "resident_id": self.resident_id,
            "start": fac.utc_iso(self.start),
            "end": fac.utc_iso(self.end),
            "minutes": self.minutes,
        }

    def since(self, t: np.ndarray, at: float) -> np.ndarray:
        return (t - at).astype(float)


class InjectedFall(Injected):
    """Out of bed with an impact spike on the first tick, then lying still on the floor for `minutes`
    (until staff help), then helped up for 4 minutes. The wristband is on throughout."""

    kind = "fall"
    HELP_S = 240

    def __init__(self, resident_id: int, start: float, minutes: float):
        super().__init__(resident_id, start, minutes)
        self.found = self.end
        self.end = self.found + self.HELP_S

    def apply(self, sig: Signals) -> None:
        t, c, rng = sig.t, sig.cols, sig.rng
        n = len(t)
        floor = t < self.found
        first_tick = t < self.start + fac.TICK_S  # a scenario only sees ticks ≥ start: this is the first one
        c["in_bed"][:] = False
        c["worn"][:] = True
        c["impact"] = np.where(first_tick, rng.uniform(4.0, 5.2), c["impact"])
        c["motion"] = np.where(floor, np.abs(rng.normal(0, 0.15, n)), 0.35 * MOTION_PER_ACTIVITY + rng.normal(0, 2, n))
        c["hr"] = c["hr"] + 18 * np.exp(-np.maximum(self.since(t, self.start), 0) / 1200)


class InjectedSepsisDrift(Injected):
    """Rosa-Alvarez-style silent drift, compressed for a live demo: over the first 90 min HR +25 bpm,
    RR +6, wrist skin temp +0.8 °C, SpO2 −2 and three times the restless movement in bed; then it holds."""

    kind = "sepsis_drift"
    RAMP_S = 90 * 60

    def apply(self, sig: Signals) -> None:
        t, c, rng = sig.t, sig.cols, sig.rng
        k = np.clip(self.since(t, self.start) / self.RAMP_S, 0.0, 1.0)
        c["hr"] = c["hr"] + 25 * k
        c["rr"] = c["rr"] + 6 * k
        c["skin_temp"] = c["skin_temp"] + 0.8 * k
        c["spo2"] = np.maximum(c["spo2"] - 2 * k, 88)
        c["hrv_rmssd"] = c["hrv_rmssd"] * (1 - 0.45 * k)
        in_bed = c["in_bed"].astype(bool)
        c["motion"] = c["motion"] + np.where(in_bed, np.abs(rng.normal(0, 0.5, len(t))) * k, 0.0)


class InjectedBandOff(Injected):
    """Wristband taken off for `minutes` (a device task, never an alarm)."""

    kind = "remove_band"

    def apply(self, sig: Signals) -> None:
        sig.cols["worn"][:] = False


class InjectedBedExit(Injected):
    """In bed for one tick (so there is an exit edge even if they were up), then out of bed, walking
    at first and then standing about, for the rest of `minutes`."""

    kind = "bed_exit"
    LEAD_S = 5  # one tick
    WALK_S = 60

    def apply(self, sig: Signals) -> None:
        t, c, rng = sig.t, sig.cols, sig.rng
        dt = self.since(t, self.start)
        lead = dt < self.LEAD_S
        walking = (dt >= self.LEAD_S) & (dt < self.LEAD_S + self.WALK_S)
        c["in_bed"] = lead.copy()
        c["worn"][:] = True
        up = ~lead
        c["motion"] = np.where(walking, WALK_MOTION + rng.normal(0, 3, len(t)), c["motion"])
        c["motion"] = np.where(up & ~walking, 0.3 * MOTION_PER_ACTIVITY + rng.normal(0, 2, len(t)), c["motion"])
        c["hr"] = c["hr"] + np.where(walking, 14.0, np.where(up, 7.0, 0.0))


INJECTORS: dict[str, type[Injected]] = {
    "fall": InjectedFall,
    "sepsis_drift": InjectedSepsisDrift,
    "remove_band": InjectedBandOff,
    "bed_exit": InjectedBedExit,
}


@dataclass
class InjectError(Exception):
    status: int
    message: str


def make(kind: str, resident_id: int, minutes: float | None = None, start: float | None = None) -> Injected:
    if kind not in INJECTORS:
        raise InjectError(400, f"unknown injector {kind!r} (one of {', '.join(KINDS)})")
    start = time.time() if start is None else start
    return INJECTORS[kind](resident_id, start, minutes or DEFAULT_MINUTES[kind])
