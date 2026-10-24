"""iPhone bed-sensor state machine: bed exit / return / restlessness (SPEC §10). Phase 7.

The `/bed/[id]` page sends one summary per second: `motion_energy` (RMS of the phone's linear acceleration
over that second, m/s²), `peak_jolt` (its peak, m/s²) and, when the browser has it, the mean gravity vector
(the phone's resting tilt). Pure code, no I/O: `app/devices/ingest.py` feeds it and writes what it returns.

Calibration (the page walks the presenter through it, speaking each step): bed empty 5 s → lie still 10 s →
shift / roll over 5 s, with unrecorded `prep` pauses for placing the phone and lying down. `Calibration.from_samples` derives every threshold from those samples:

* `quiet_energy`: below it the bed is still (2× the lying-still p95, never below the phone's noise floor)
* `burst_jolt`: getting in or out, or rolling over, peaks at or above it (half the shift's peak, floored)
* tilt: under a pillow or a topper the weight of a body tips the phone. When the empty and occupied resting
  angles are well apart (≥ 4× their noise), tilt says directly whether the bed is occupied: 2 s of
  "empty" tilt confirms an exit, 2 s of "occupied" tilt a return.
* energy: when lying still is clearly busier than the empty bed (breathing, heartbeat), a burst followed by
  4 s of empty-bed quiet confirms an exit; a burst followed by 2 s of lying-in-bed activity, a return.

Without either (no empty-bed step, or a phone too deep to tell), the SPEC rule applies: a burst followed by
≥ 10 s of stillness is an exit, and another burst is a return. Uncalibrated, the same rule runs on defaults.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

Vec = tuple[float, float, float]
Phase = Literal["empty", "still", "shift"]
PHASES: tuple[Phase, ...] = ("empty", "still", "shift")
PREP = "prep"  # between recorded phases (placing the phone, lying down): calibrating, nothing recorded

QUIET_FLOOR = 0.02  # m/s²: an iPhone accelerometer at rest reads ~0.005–0.01 RMS
BURST_FLOOR = 0.3  # m/s²
DEFAULT_QUIET = 0.05
DEFAULT_BURST = 0.8
MIN_SAMPLES = 3  # per calibration phase
TILT_MIN_SEP_DEG = 0.4
TILT_NOISE_FACTOR = 4.0
ENERGY_SEP_FACTOR = 2.5
EMPTY_TILT_FRAC, OCCUPIED_TILT_FRAC = 0.3, 0.6  # of the empty↔occupied angle

EXIT_STILL_S = 10  # SPEC: a burst, then ≥ 10 s of stillness
BURST_SETTLE_S = 5  # the quiet run must start within this of the burst (the burst may last a few seconds)
TILT_CONFIRM_S = 2
ENERGY_CONFIRM_S = 4
RETURN_CONFIRM_S = 2
BURST_WINDOW_S = 30  # an energy-confirmed exit/return must follow a burst within this
RESTLESS_WINDOW_S = 60
BED_MOTION_MAX = 10.0
RESTLESS_LEVELS = ("still", "some movement", "restless", "very restless")


@dataclass(frozen=True)
class Summary:
    """One second of phone motion."""

    ts: float  # epoch s
    energy: float  # RMS linear acceleration, m/s²
    jolt: float  # peak linear acceleration, m/s²
    gravity: Vec | None = None  # mean acceleration including gravity, m/s²


def angle_deg(a: Sequence[float], b: Sequence[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    # atan2 of |a×b| and a·b stays accurate for the tiny angles a mattress produces (acos does not)
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(va, vb))), float(va @ vb)))


def _mean_vec(samples: Sequence[Summary]) -> Vec | None:
    vecs = [s.gravity for s in samples if s.gravity is not None]
    if len(vecs) < MIN_SAMPLES or len(vecs) < len(samples) / 2:
        return None
    m = np.mean(np.asarray(vecs, dtype=float), axis=0)
    return float(m[0]), float(m[1]), float(m[2])


def _rms_angle(samples: Sequence[Summary], ref: Vec) -> float:
    angles = [angle_deg(s.gravity, ref) for s in samples if s.gravity is not None]
    return float(np.sqrt(np.mean(np.square(angles)))) if angles else 0.0


@dataclass(frozen=True)
class Calibration:
    quiet_energy: float = DEFAULT_QUIET
    burst_jolt: float = DEFAULT_BURST
    empty_gravity: Vec | None = None
    tilt_sep_deg: float = 0.0
    tilt_noise_deg: float = 0.0
    empty_energy: float | None = None  # p95 with the bed empty
    still_energy: float | None = None  # p50 lying still
    calibrated: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def tilt_ok(self) -> bool:
        need = max(TILT_MIN_SEP_DEG, TILT_NOISE_FACTOR * self.tilt_noise_deg)
        return self.empty_gravity is not None and self.tilt_sep_deg >= need

    @property
    def energy_ok(self) -> bool:
        if self.empty_energy is None or self.still_energy is None:
            return False
        return self.still_energy >= ENERGY_SEP_FACTOR * max(self.empty_energy, 1e-4)

    @property
    def mode(self) -> Literal["tilt", "energy", "burst"]:
        return "tilt" if self.tilt_ok else "energy" if self.energy_ok else "burst"

    @property
    def empty_ceiling(self) -> float:
        """Energy below this looks like an empty bed (geometric midpoint of empty and lying still)."""
        return math.sqrt(max(self.empty_energy or 0.0, 1e-6) * max(self.still_energy or 0.0, 1e-6))

    def tilt_from_empty(self, g: Vec | None) -> float | None:
        return None if g is None or self.empty_gravity is None else angle_deg(g, self.empty_gravity)

    def looks_empty_tilt(self, g: Vec | None) -> bool:
        a = self.tilt_from_empty(g)
        return a is not None and a <= max(EMPTY_TILT_FRAC * self.tilt_sep_deg, 2 * self.tilt_noise_deg)

    def looks_occupied_tilt(self, g: Vec | None) -> bool:
        a = self.tilt_from_empty(g)
        return a is not None and a >= OCCUPIED_TILT_FRAC * self.tilt_sep_deg

    @classmethod
    def from_samples(
        cls, still: Sequence[Summary], shift: Sequence[Summary] = (), empty: Sequence[Summary] = ()
    ) -> Calibration:
        """Thresholds from the calibration phases. Lying still is required; shift and empty refine it."""
        if len(still) < MIN_SAMPLES:
            raise ValueError(f"need at least {MIN_SAMPLES} s of lying still (got {len(still)})")
        warnings: list[str] = []
        still_e = np.array([s.energy for s in still])
        still_j = np.array([s.jolt for s in still])
        quiet = max(QUIET_FLOOR, 2.0 * float(np.percentile(still_e, 95)))
        burst = max(BURST_FLOOR, 3.0 * float(np.percentile(still_j, 95)), 1.5 * quiet)
        if shift:
            peak = max(s.jolt for s in shift)
            if peak >= burst:  # getting in or out is at least half as vigorous as a deliberate shift
                burst = max(burst, 0.5 * peak)
            else:
                warnings.append(
                    "The shift barely registered: put the phone closer to where you lie (under the pillow)."
                )
        else:
            burst = max(burst, DEFAULT_BURST)

        empty_gravity, sep, noise, empty_energy = None, 0.0, 0.0, None
        if len(empty) >= MIN_SAMPLES:
            empty_energy = float(np.percentile([s.energy for s in empty], 95))
            e_vec, o_vec = _mean_vec(empty), _mean_vec(still)
            if e_vec is not None and o_vec is not None:
                empty_gravity = e_vec
                sep = angle_deg(e_vec, o_vec)
                noise = max(_rms_angle(empty, e_vec), _rms_angle(still, o_vec))
        cal = cls(
            quiet_energy=round(quiet, 4),
            burst_jolt=round(burst, 4),
            empty_gravity=empty_gravity,
            tilt_sep_deg=round(sep, 3),
            tilt_noise_deg=round(noise, 4),
            empty_energy=None if empty_energy is None else round(empty_energy, 4),
            still_energy=round(float(np.percentile(still_e, 50)), 4),
            calibrated=True,
        )
        if cal.mode == "burst":
            if not empty:
                warnings.append("No empty-bed step: exits are confirmed after 10 s of stillness.")
            else:
                warnings.append(
                    "Lying down barely changes the phone's tilt or motion here: exits are confirmed after 10 s "
                    "of stillness. A phone under the pillow tells faster."
                )
        return replace(cal, warnings=tuple(warnings))

    def to_dict(self) -> dict:
        return {
            "quiet_energy": self.quiet_energy,
            "burst_jolt": self.burst_jolt,
            "empty_gravity": list(self.empty_gravity) if self.empty_gravity else None,
            "tilt_sep_deg": self.tilt_sep_deg,
            "tilt_noise_deg": self.tilt_noise_deg,
            "empty_energy": self.empty_energy,
            "still_energy": self.still_energy,
            "calibrated": self.calibrated,
            "warnings": list(self.warnings),
        }

    def describe(self) -> dict:
        return self.to_dict() | {"mode": self.mode}

    @classmethod
    def from_dict(cls, d: dict) -> Calibration:
        g = d.get("empty_gravity")
        return cls(
            quiet_energy=float(d.get("quiet_energy", DEFAULT_QUIET)),
            burst_jolt=float(d.get("burst_jolt", DEFAULT_BURST)),
            empty_gravity=(float(g[0]), float(g[1]), float(g[2])) if g else None,
            tilt_sep_deg=float(d.get("tilt_sep_deg", 0.0)),
            tilt_noise_deg=float(d.get("tilt_noise_deg", 0.0)),
            empty_energy=d.get("empty_energy"),
            still_energy=d.get("still_energy"),
            calibrated=bool(d.get("calibrated", False)),
            warnings=tuple(d.get("warnings") or ()),
        )


@dataclass(frozen=True)
class Transition:
    kind: Literal["bed_exit", "bed_return"]
    ts: float  # when it happened (the burst or the first second of evidence), not when it was confirmed
    reason: str  # tilt | energy | burst | calibration | admin

    def payload(self) -> dict:
        return {"device": "bed_sensor", "source": "phone", "detected_by": self.reason}


@dataclass(frozen=True)
class Reading:
    """What to store for this second, and what the phone page shows."""

    ts: float
    in_bed: bool | None  # None while calibrating
    bed_motion: float  # 0–10, the simulator's bed-movement scale
    level: int  # restlessness 0–3 over the last minute
    calibrating: str | None  # a Phase or PREP
    transition: Transition | None = None

    @property
    def state(self) -> str:
        if self.calibrating:
            return f"calibrating: {self.calibrating}"
        if not self.in_bed:
            return "out of bed"
        return f"in bed · {RESTLESS_LEVELS[self.level]}"


@dataclass
class BedTracker:
    cal: Calibration = field(default_factory=Calibration)
    in_bed: bool = True
    calibrating: str | None = None  # a Phase or PREP
    samples: dict[str, list[Summary]] = field(default_factory=dict)
    last_burst: float | None = None
    quiet_since: float | None = None
    last_change: float | None = None  # ts of the newest transition
    streaks: dict[str, int] = field(default_factory=dict)  # consecutive seconds a condition held
    streak_start: dict[str, float] = field(default_factory=dict)
    recent: deque = field(default_factory=lambda: deque(maxlen=RESTLESS_WINDOW_S))

    # ---- calibration

    def begin_calibration(self, phase: str) -> None:
        """Record the following summaries as `phase`, or (PREP) hold without recording."""
        if phase not in (*PHASES, PREP):
            raise ValueError(f"unknown calibration phase {phase!r}")
        if self.calibrating is None:
            self.samples = {}
        self.calibrating = phase
        if phase != PREP:
            self.samples.setdefault(phase, [])

    def finish_calibration(self, ts: float) -> tuple[Calibration, Transition | None]:
        """Compute the thresholds; the presenter is lying in bed now (the last phase was in bed)."""
        cal = Calibration.from_samples(
            self.samples.get("still", []), self.samples.get("shift", []), self.samples.get("empty", [])
        )
        self.cal = cal
        self.calibrating = None
        self.samples = {}
        self._reset()
        return cal, self._set(True, ts, "calibration")

    def cancel_calibration(self) -> None:
        self.calibrating = None
        self.samples = {}
        self._reset()

    # ---- state

    def force(self, in_bed: bool, ts: float, reason: str = "admin") -> Transition | None:
        """Set the state from outside (the admin fallback button)."""
        self._reset()
        return self._set(in_bed, ts, reason)

    def _set(self, in_bed: bool, ts: float, reason: str) -> Transition | None:
        if in_bed == self.in_bed:
            return None
        self.in_bed = in_bed
        self.last_change = ts
        return Transition("bed_return" if in_bed else "bed_exit", ts, reason)

    def _reset(self) -> None:
        self.last_burst = None
        self.quiet_since = None
        self.streaks.clear()
        self.streak_start.clear()

    def _streak(self, name: str, ok: bool, ts: float) -> int:
        if ok:
            self.streaks[name] = self.streaks.get(name, 0) + 1
            self.streak_start.setdefault(name, ts)
        else:
            self.streaks.pop(name, None)
            self.streak_start.pop(name, None)
        return self.streaks.get(name, 0)

    def step(self, s: Summary) -> Reading:
        cal = self.cal
        moving = s.energy >= cal.quiet_energy
        burst = s.jolt >= cal.burst_jolt
        if burst:
            self.last_burst = s.ts
        if moving or burst:
            self.quiet_since = None
        elif self.quiet_since is None:
            self.quiet_since = s.ts

        if self.calibrating is not None:
            if self.calibrating != PREP:
                self.samples[self.calibrating].append(s)
            return Reading(s.ts, None, self._bed_motion(s), 0, self.calibrating)

        empty_tilt = self._streak("empty_tilt", cal.tilt_ok and cal.looks_empty_tilt(s.gravity), s.ts)
        occ_tilt = self._streak("occ_tilt", cal.tilt_ok and cal.looks_occupied_tilt(s.gravity), s.ts)
        empty_energy = self._streak("empty_energy", cal.energy_ok and s.energy < cal.empty_ceiling, s.ts)
        busy_energy = self._streak("busy_energy", cal.energy_ok and s.energy >= cal.empty_ceiling, s.ts)
        recent_burst = self.last_burst is not None and s.ts - self.last_burst <= BURST_WINDOW_S
        if self.last_change is not None and self.last_burst is not None and self.last_burst <= self.last_change:
            recent_burst = False  # the burst that caused the last transition doesn't count twice

        mode = cal.mode
        if mode == "tilt" and s.gravity is None:  # this browser stopped reporting tilt: use the next-best rule
            mode = "energy" if cal.energy_ok else "burst"
        t: Transition | None = None
        if self.in_bed:
            if mode == "tilt" and empty_tilt >= TILT_CONFIRM_S:
                t = self._set(False, self.streak_start["empty_tilt"], "tilt")
            elif mode == "energy" and empty_energy >= ENERGY_CONFIRM_S and recent_burst:
                t = self._set(False, self.last_burst, "energy")
            elif mode == "burst" and self._burst_then_still(s.ts, recent_burst):
                t = self._set(False, self.last_burst, "burst")
        else:
            if mode == "tilt" and occ_tilt >= RETURN_CONFIRM_S:
                t = self._set(True, self.streak_start["occ_tilt"], "tilt")
            elif mode == "energy" and busy_energy >= RETURN_CONFIRM_S and recent_burst:
                t = self._set(True, self.last_burst, "energy")
            elif mode == "burst" and burst and recent_burst:
                t = self._set(True, s.ts, "burst")
        if t is not None:
            self._reset()

        self.recent.append(moving and self.in_bed)
        level = self._level() if self.in_bed else 0
        return Reading(s.ts, self.in_bed, self._bed_motion(s) if self.in_bed else 0.0, level, None, t)

    def _burst_then_still(self, ts: float, recent_burst: bool) -> bool:
        if not recent_burst or self.quiet_since is None or self.last_burst is None:
            return False
        settled = 0 <= self.quiet_since - self.last_burst <= BURST_SETTLE_S
        return settled and ts - self.quiet_since + 1 >= EXIT_STILL_S

    def _bed_motion(self, s: Summary) -> float:
        return round(min(BED_MOTION_MAX, s.energy / self.cal.quiet_energy), 2)

    def _level(self) -> int:
        if not self.recent:
            return 0
        frac = sum(self.recent) / len(self.recent)
        return 0 if frac < 0.05 else 1 if frac < 0.2 else 2 if frac < 0.5 else 3

    def describe(self) -> dict:
        return {
            "in_bed": self.in_bed,
            "calibrating": self.calibrating,
            "calibration": self.cal.describe(),
        }
