"""Normal per-resident physiology from a seeded RNG (SPEC §8).

* `PhysioParams`: stable per-resident parameters drawn from (seed, resident_id).
* `DayPlan`: one resident's local noon-to-noon schedule: bed and nap, bathroom trips, turning, band
  charging, and the staff events around them (round checks, meals/fluids, spot checks). Plans are
  deterministic per (seed, resident, date), so any time range can be regenerated identically.
* `ResidentSim.step()`: turns plans into 5-second rows plus bed and staff events. AR(1) noise and
  smoothed sleep/activity carry over between calls, so the live loop continues seamlessly.
* `Scenario`: hook used by the scripted cast (and later the injectors) to rewrite a plan and/or signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import NamedTuple
from zoneinfo import ZoneInfo

import numpy as np

from app.sim import facility as fac
from app.sim.facility import TICK_S

SPO2_PERIOD_S = 300
HRV_PERIOD_S = 300
SKIN_PERIOD_S = 60
STILL_MOTION_PER_MIN = 6.0  # wrist motion per minute below this is "still" (same constant as 004_jobs.sql)
MOTION_PER_ACTIVITY = 40.0  # wrist counts per 5 s at full activity
PLAN_EVENT_MARGIN_S = 2 * 3600

SOURCE_SIM = 0


# --------------------------------------------------------------------------- parameters


@dataclass(frozen=True)
class PhysioParams:
    resident_id: int
    unit: str
    room_index: int  # 0–39: position on the unit's round
    mobility: str
    cognition: str
    morse: int = 0
    afib: bool = False
    copd: bool = False
    beta_blocker: bool = False
    hr_base: float = 72.0  # awake resting HR
    hr_sleep_drop: float = 0.10  # fraction lower when asleep (8–12 %)
    hr_activity_gain: float = 20.0  # bpm added at full activity (≤ 25)
    hr_noise_sd: float = 2.0
    hr_phi: float = 0.9  # AR(1) coefficient per 5-s tick
    hr_min: float = 42.0
    hr_max: float = 125.0
    rr_base: float = 16.0
    spo2_base: float = 96.0
    spo2_lo: float = 92.0
    spo2_hi: float = 100.0
    skin_base: float = 33.6
    hrv_base: float = 28.0
    activity: float = 0.6  # daytime activity scale
    bedtime_offset_min: float = 0.0  # vs 20:30
    wake_offset_min: float = 0.0  # vs 06:30
    nap_prob: float = 0.5
    trips_per_night: float = 1.0  # mean bathroom trips per night
    turn_cadence_min: float = 60.0  # minutes between turns while asleep
    charge_hour: float = 8.5  # local hour the band comes off for charging / shower
    restless: float = 1.0  # > 1: more small movements at night (sundowning)
    assist_prob: float = 0.85  # night bathroom trip preceded by a staff check (call light answered)
    appetite: float = 80.0  # usual % of meal eaten
    sbp_base: float = 128.0
    temp_base: float = 36.7
    phase_s: int = 0  # offset of the 1-min / 5-min sampling cadence, spreads load across ticks

    @property
    def bedbound(self) -> bool:
        return self.mobility == "bedbound"

    @property
    def fall_precautions(self) -> bool:
        """Gets up only with staff (mobility ≠ independent or Morse ≥ 45): the fast tier's fall-prevention set."""
        return not self.bedbound and (self.mobility != "independent" or self.morse >= 45)


_ACTIVITY_RANGE = {"independent": (0.6, 1.0), "1-assist": (0.4, 0.7), "2-assist": (0.2, 0.4), "bedbound": (0.05, 0.12)}
_ASSIST_PROB = {"independent": 0.0, "1-assist": 0.85, "2-assist": 0.95, "bedbound": 0.0}


def make_params(
    seed: int,
    resident_id: int,
    *,
    unit: str,
    room_index: int,
    mobility: str,
    cognition: str,
    morse: int = 0,
    flags: frozenset[str] | set[str] | tuple[str, ...] = (),
    **overrides: float,
) -> PhysioParams:
    """Stable per-resident parameters (SPEC §8). Draw order is fixed, so overrides never shift other draws."""
    rng = np.random.default_rng([seed, resident_id, 101])
    u = rng.uniform
    afib, copd, beta_blocker = "afib" in flags, "copd" in flags, "beta_blocker" in flags

    hr_base, gain, noise, phi = u(58, 84), u(15, 25), u(1.5, 2.5), 0.9
    hr_min, hr_max, drop = 42.0, 125.0, u(0.08, 0.12)
    if beta_blocker:  # blunted rate and response
        hr_base, gain, noise, hr_max = u(52, 64), u(8, 14), u(1.2, 1.8), 95.0
    if afib:  # fast, irregular, little night dip
        hr_base, gain, noise, phi, hr_min, hr_max, drop = u(96, 102), u(8, 12), 7.0, 0.5, 82.0, 122.0, u(0.04, 0.06)

    if copd:
        spo2_base, spo2_lo, spo2_hi = u(89.0, 90.5), 88.0, 92.0
    else:
        spo2_base, spo2_lo, spo2_hi = u(94.5, 97.5), 92.0, 100.0

    lo, hi = _ACTIVITY_RANGE[mobility]
    bedbound = mobility == "bedbound"
    params = PhysioParams(
        resident_id=resident_id,
        unit=unit,
        room_index=room_index,
        mobility=mobility,
        cognition=cognition,
        morse=morse,
        afib=afib,
        copd=copd,
        beta_blocker=beta_blocker,
        hr_base=hr_base,
        hr_sleep_drop=drop,
        hr_activity_gain=gain,
        hr_noise_sd=noise,
        hr_phi=phi,
        hr_min=hr_min,
        hr_max=hr_max,
        rr_base=u(14, 18) + (1.0 if copd else 0.0),
        spo2_base=spo2_base,
        spo2_lo=spo2_lo,
        spo2_hi=spo2_hi,
        skin_base=u(33.0, 34.0),
        hrv_base=u(70, 110) if afib else u(15, 40),
        activity=u(lo, hi),
        bedtime_offset_min=float(np.clip(rng.normal(0, 25), -45, 45)),
        wake_offset_min=float(np.clip(rng.normal(0, 25), -45, 45)),
        nap_prob=u(0.2, 0.8),
        trips_per_night=0.0 if bedbound else u(0.3, 2.5),
        turn_cadence_min=u(30, 90),
        charge_hour=u(7.0, 10.5),
        restless=u(1.2, 1.8) if unit == "Memory Care" else 1.0,
        assist_prob=_ASSIST_PROB[mobility] * (0.7 if cognition in ("moderate", "severe") else 1.0),
        appetite=u(55, 95),
        sbp_base=u(108, 150),
        temp_base=u(36.4, 37.0),
        phase_s=int(rng.integers(0, SPO2_PERIOD_S // TICK_S)) * TICK_S,
    )
    return replace(params, **overrides)


# --------------------------------------------------------------------------- day plans


class SimEvent(NamedTuple):
    ts: float  # epoch seconds
    resident_id: int
    type: str
    staff_id: int | None
    payload: dict


@dataclass
class Trip:
    """Out of bed (bathroom). A fall trip: walking until `fall_at`, still on the floor until `found_at`,
    then helped back to bed by `end`."""

    start: float
    end: float
    assisted: bool = False
    fall_at: float | None = None
    found_at: float | None = None


@dataclass
class DayPlan:
    resident_id: int
    day: date  # local date of the noon that opens the window
    start: float  # epoch s, local noon
    end: float  # epoch s, next local noon
    bed: tuple[float, float]  # in bed (bedbound: the whole window)
    sleep: tuple[float, float]  # asleep at night (settled → wake)
    nap: tuple[float, float] | None
    trips: list[Trip]
    turns: list[float]  # self repositioning while asleep
    fidgets: list[float]  # small movements while asleep
    staff_turns: list[float]  # repositioned by staff (bedbound)
    band_off: list[tuple[float, float]]
    events: list[SimEvent] = field(default_factory=list)
    night_motion: float = 1.0  # multiplier on micro-movement while asleep at night

    def at(self, hours: float, tz: ZoneInfo) -> float:
        """Epoch of `hours` after local midnight of `day` (so 26.25 = 02:15 the next morning)."""
        return fac.local_epoch(self.day, hours, tz)


def _hour_of(epoch: float, anchor_epoch: float, anchor_hour: float) -> float:
    return (anchor_hour + (epoch - anchor_epoch) / 3600) % 24


FIDGETS_PER_NIGHT = 3.0  # small movements while asleep, × the resident's `restless` trait


def assist_check(rid: int, unit: str, ts: float, midnight: float, reason: str = "assisted to bathroom") -> SimEvent:
    """The CNA on duty answers the call light / bed alarm at `ts`, just before the resident gets up.

    `midnight` is the epoch of any local midnight, used to find the shift on duty."""
    staff = fac.staff_on_duty("CNA", _hour_of(ts, midnight, 0), unit)
    return SimEvent(ts, rid, "check_logged", staff, {"kind": "assist", "reason": reason})


def make_day_plan(p: PhysioParams, day: date, seed: int, tz: ZoneInfo) -> DayPlan:
    """Normal behavior + staff schedule for the noon-to-noon window that starts on `day`."""
    rng = np.random.default_rng([seed, p.resident_id, day.toordinal(), 202])

    def at(hours: float) -> float:
        return fac.local_epoch(day, hours, tz)

    start, end = at(12), at(36)
    bedtime = at(20.5 + (p.bedtime_offset_min + rng.normal(0, 15)) / 60)
    wake = at(30.5 + (p.wake_offset_min + rng.normal(0, 15)) / 60)
    settled = bedtime + rng.uniform(10, 30) * 60
    nap = None
    if rng.random() < p.nap_prob:
        nap_start = at(13 + rng.uniform(0, 1.5))
        nap = (nap_start, nap_start + rng.uniform(40, 90) * 60)

    trips: list[Trip] = []
    n_trips = 0 if p.bedbound else min(int(rng.poisson(p.trips_per_night)), 4)
    last_end = -np.inf
    for s in np.sort(rng.uniform(settled + 45 * 60, wake - 30 * 60, size=n_trips)):
        dur = rng.uniform(3, 8) * 60
        assisted = bool(rng.random() < p.assist_prob)
        if s >= last_end + 20 * 60:
            trips.append(Trip(float(s), float(s + dur), assisted=assisted))
            last_end = s + dur

    turns, t = [], settled + rng.uniform(0.3, 1.0) * p.turn_cadence_min * 60
    while t < wake:
        turns.append(float(t))
        t += p.turn_cadence_min * 60 * rng.uniform(0.6, 1.4)
    fidgets = sorted(float(x) for x in rng.uniform(settled, wake, size=rng.poisson(FIDGETS_PER_NIGHT * p.restless)))

    charge = at(24 + p.charge_hour + rng.normal(0, 20) / 60)
    band_off = [(charge, charge + rng.uniform(25, 40) * 60)]

    plan = DayPlan(
        resident_id=p.resident_id,
        day=day,
        start=start,
        end=end,
        bed=(start, end) if p.bedbound else (bedtime, wake),
        sleep=(settled, wake),
        nap=nap,
        trips=trips,
        turns=turns,
        fidgets=fidgets,
        staff_turns=[],
        band_off=band_off,
    )
    _add_staff_events(plan, p, rng, at)
    return plan


_ROUTINE_NOTES = {
    "default": (
        "Resting quietly, no concerns noted.",
        "Ate most of dinner, visited with family in the lounge.",
        "Repositioned for comfort, skin intact.",
        "Up to the bathroom with assist, steady on feet.",
        "Pleasant and cooperative with evening care.",
        "Slept well between rounds, no complaints.",
    ),
    "Memory Care": (
        "Calm this evening, joined the music group.",
        "Mild sundowning after dinner, redirected easily with a snack.",
        "Resting quietly, no concerns noted.",
        "Needed reminders to use the call light, otherwise at baseline.",
    ),
}


def _add_staff_events(plan: DayPlan, p: PhysioParams, rng: np.random.Generator, at) -> None:
    rid, events = p.resident_id, plan.events
    unit = p.unit

    def cna(epoch: float, anchor: float, hour: float) -> int:
        return fac.staff_on_duty("CNA", _hour_of(epoch, anchor, hour), unit)

    # Round checks every 2 h; a round walks the unit's rooms from −20 to +20 minutes around the hour.
    for r in range(12, 36, 2):
        hour = r % 24
        night = hour in fac.NIGHT_ROUND_HOURS
        skip = rng.random() < (0.04 if night else 0.02)
        jitter = rng.normal(0, 90)
        if skip:
            continue
        ts = at(r) + ((-fac.ROUND_SPREAD_MIN / 2) + p.room_index / 40 * fac.ROUND_SPREAD_MIN) * 60 + jitter
        payload = {"kind": "round", "round": f"{hour:02d}:00"}
        if p.bedbound:
            payload["repositioned"] = True
            plan.staff_turns.append(ts + 60)
        events.append(SimEvent(ts, rid, "check_logged", cna(ts, at(r), hour), payload))

    # Assisted bathroom trips: the CNA answers the call light just before the resident gets up.
    for trip in plan.trips:
        if trip.assisted:
            events.append(assist_check(rid, unit, trip.start - rng.uniform(60, 180), at(24)))

    # Residents on fall precautions get up from a nap or in the morning with staff (morning care).
    if p.fall_precautions:
        if plan.nap:
            events.append(assist_check(rid, unit, plan.nap[1] - rng.uniform(60, 180), at(24), "up from nap"))
        events.append(assist_check(rid, unit, plan.bed[1] - rng.uniform(60, 180), at(24), "morning care"))

    # Meals and fluid passes (logged by the CNA).
    for hour, meal in ((12.75, "lunch"), (17.75, "dinner"), (32.75, "breakfast")):
        ts = at(hour) + rng.normal(0, 8 * 60)
        pct = int(np.clip(round(rng.normal(p.appetite, 12) / 25) * 25, 0, 100))
        fluid = int(np.clip(round(rng.normal(240, 50) / 10) * 10, 60, 400))
        payload = {"kind": "meal", "meal": meal, "meal_pct": pct, "fluid_ml": fluid}
        events.append(SimEvent(ts, rid, "intake_logged", cna(ts, at(hour), hour % 24), payload))
    for hour in (14.5, 20.0, 34.5):
        ts = at(hour) + rng.normal(0, 10 * 60)
        fluid = int(np.clip(round(rng.normal(180, 40) / 10) * 10, 60, 300))
        events.append(
            SimEvent(ts, rid, "intake_logged", cna(ts, at(hour), hour % 24), {"kind": "fluids", "fluid_ml": fluid})
        )

    # BP + core temperature spot checks twice a day (LPN), walked room by room.
    for hour, temp_shift in ((20.0, 0.0), (33.0, -0.2)):
        ts = at(hour) + p.room_index / 40 * 3600 + rng.normal(0, 5 * 60)
        sbp = int(round(rng.normal(p.sbp_base, 8)))
        payload = {
            "sbp": sbp,
            "dbp": int(round(sbp * 0.58 + rng.normal(0, 4))),
            "temp_c": round(float(rng.normal(p.temp_base + temp_shift, 0.15)), 1),
            "method": "oral",
        }
        lpn = fac.staff_on_duty("LPN", _hour_of(ts, at(hour), hour % 24))
        events.append(SimEvent(ts, rid, "spot_check", lpn, payload))

    # The occasional routine note.
    if rng.random() < 0.08:
        hour = rng.uniform(19, 30)
        ts = at(hour)
        texts = _ROUTINE_NOTES.get(unit, _ROUTINE_NOTES["default"])
        text = texts[int(rng.integers(0, len(texts)))]
        payload = {"text": text, "author_role": "CNA", "source": "text", "routine": True}
        events.append(SimEvent(ts, rid, "note_added", cna(ts, at(hour), hour % 24), payload))

    events.sort(key=lambda e: e.ts)


# --------------------------------------------------------------------------- scenarios


@dataclass
class Signals:
    """What a scenario sees and may change: raw (unrounded, unmasked) signals for one resident."""

    t: np.ndarray  # epoch seconds, int64
    params: PhysioParams
    sleep_depth: np.ndarray  # 0 awake … 1 asleep, smoothed
    activity: np.ndarray  # smoothed activity 0..1 (drives HR)
    hr_expected: np.ndarray  # personal normal HR without noise or activity
    cols: dict[str, np.ndarray]  # hr, hrv_rmssd, skin_temp, spo2, motion, impact, worn, rr, in_bed, bed_motion
    rng: np.random.Generator


class Scenario:
    """Base class: a time-bounded change to one resident's plan and/or signals."""

    resident_id: int
    start: float = -np.inf
    end: float = np.inf

    def adjust_plan(self, plan: DayPlan, params: PhysioParams, tz: ZoneInfo) -> None:  # noqa: B027
        """Rewrite a day plan in place (called for every plan of this resident)."""

    def apply(self, sig: Signals) -> None:  # noqa: B027
        """Modify signals in place (called only when the batch overlaps [start, end))."""


# --------------------------------------------------------------------------- signal synthesis


def ar1(eps: np.ndarray, phi: float, x0: float = 0.0, block: int = 64) -> np.ndarray:
    """x_t = phi·x_{t-1} + eps_t, vectorized in blocks (exact; phi in (0, 1])."""
    n = len(eps)
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    k = np.arange(block, dtype=np.float64)
    inv, pw = phi**-k, phi**k
    x = x0
    for s in range(0, n, block):
        e = eps[s : s + block]
        m = len(e)
        seg = pw[:m] * (phi * x + np.cumsum(e * inv[:m]))
        out[s : s + m] = seg
        x = seg[-1]
    return out


def ema(x: np.ndarray, alpha: float, x0: float) -> np.ndarray:
    return ar1(alpha * np.asarray(x, dtype=np.float64), 1.0 - alpha, x0)


def _span(t0: int, n: int, start: float, end: float) -> slice:
    i0 = int(np.clip(np.ceil((start - t0) / TICK_S), 0, n))
    i1 = int(np.clip(np.ceil((end - t0) / TICK_S), 0, n))
    return slice(i0, max(i0, i1))


def _index(t0: int, n: int, ts: float) -> int | None:
    i = int(np.ceil((ts - t0) / TICK_S))
    return i if 0 <= i < n else None


def _bump(hours: np.ndarray, center: float, width: float = 0.6) -> np.ndarray:
    return np.exp(-0.5 * ((hours - center) / width) ** 2)


@dataclass
class ResidentState:
    """Carried between step() calls: noise memory, smoothed sleep/activity, last bed state."""

    rng: np.random.Generator
    hr_noise: float = 0.0
    rr_noise: float = 0.0
    skin_noise: float = 0.0
    act_noise: float = 0.0
    sleep_depth: float | None = None
    act_smooth: float = 0.0
    last_in_bed: bool | None = None


@dataclass
class SimBatch:
    cols: dict[str, np.ndarray]  # VITALS_COLUMNS layout (ts in unix µs, NaN / -1 = NULL)
    events: list[SimEvent]

    @property
    def rows(self) -> int:
        return len(self.cols["ts"])


VITAL_KEYS = ("hr", "hrv_rmssd", "skin_temp", "spo2", "motion", "impact", "worn", "rr", "in_bed", "bed_motion")


class ResidentSim:
    """Simulates one resident over consecutive time ranges."""

    def __init__(self, params: PhysioParams, seed: int, tz: ZoneInfo, scenarios: list[Scenario] | None = None):
        self.params = params
        self.seed = seed
        self.tz = tz
        self.scenarios: list[Scenario] = list(scenarios or [])
        self.state = ResidentState(rng=np.random.default_rng([seed, params.resident_id, 303]))
        self._plans: dict[int, DayPlan] = {}

    def add_scenario(self, scenario: Scenario) -> None:
        self.scenarios.append(scenario)
        self._plans.clear()

    def plan(self, day_number: int) -> DayPlan:
        plan = self._plans.get(day_number)
        if plan is None:
            plan = make_day_plan(self.params, fac.day_from_number(day_number), self.seed, self.tz)
            for sc in self.scenarios:
                sc.adjust_plan(plan, self.params, self.tz)
            plan.events.sort(key=lambda e: e.ts)
            if len(self._plans) > 8:  # live loop: keep the cache small
                self._plans.pop(min(self._plans))
            self._plans[day_number] = plan
        return plan

    def step(self, t0: int, n: int) -> SimBatch:
        """Simulate n ticks starting at epoch second t0 (a multiple of TICK_S)."""
        p, st, rng = self.params, self.state, self.state.rng
        t = t0 + TICK_S * np.arange(n, dtype=np.int64)
        offsets = fac.utc_offsets(t, self.tz)
        hours = fac.local_hours(t, offsets)
        # Also consult the neighbouring plans: a few staff events sit just outside their own noon-to-noon
        # window (the 12:00 round starts at 11:40), and a 1-tick live step must still emit them.
        edges = np.array([t0 - PLAN_EVENT_MARGIN_S, t0 + n * TICK_S + PLAN_EVENT_MARGIN_S], dtype=np.int64)
        days = np.union1d(fac.window_dates(t, offsets), fac.window_dates(edges, fac.utc_offsets(edges, self.tz)))
        plans = [self.plan(int(d)) for d in days]

        in_bed = np.zeros(n, bool)
        asleep = np.zeros(n, bool)
        walking = np.zeros(n, bool)
        on_floor = np.zeros(n, bool)
        helped = np.zeros(n, bool)
        worn = np.ones(n, bool)
        night_mult = np.ones(n)
        wrist_spike = np.zeros(n)
        bed_spike = np.zeros(n)
        impact = np.full(n, np.nan)

        for plan in plans:
            in_bed[_span(t0, n, *plan.bed)] = True
            sleep = _span(t0, n, *plan.sleep)
            asleep[sleep] = True
            night_mult[sleep] = plan.night_motion
            if plan.nap:
                in_bed[_span(t0, n, *plan.nap)] = True
                asleep[_span(t0, n, *plan.nap)] = True
            for trip in plan.trips:
                out = _span(t0, n, trip.start, trip.end)
                in_bed[out] = asleep[out] = False
                walking[_span(t0, n, trip.start, trip.fall_at or trip.end)] = True
                if trip.fall_at is not None:
                    found = trip.found_at or trip.end
                    on_floor[_span(t0, n, trip.fall_at, found)] = True
                    helped[_span(t0, n, found, trip.end)] = True
                    if (i := _index(t0, n, trip.fall_at)) is not None:
                        impact[i] = rng.uniform(3.8, 5.2)
            for ts in plan.turns + plan.staff_turns:
                for k in (0, 1):
                    if (i := _index(t0, n, ts + k * TICK_S)) is not None:
                        wrist_spike[i] += rng.uniform(15, 40)
                        bed_spike[i] += rng.uniform(20, 50)
            for ts in plan.fidgets:
                if (i := _index(t0, n, ts)) is not None:
                    wrist_spike[i] += rng.uniform(4, 9)
                    bed_spike[i] += rng.uniform(5, 12)
            for start, end in plan.band_off:
                worn[_span(t0, n, start, end)] = False
        walking &= ~on_floor
        asleep &= in_bed
        wrist_spike *= in_bed
        bed_spike *= in_bed

        # Activity: meals drive daytime peaks; asleep ≈ 0; walking to the bathroom ≈ 0.7.
        act_noise = ar1(rng.normal(0, 0.45 * np.sqrt(1 - 0.98**2), n), 0.98, st.act_noise)
        day_curve = np.where(
            (hours >= 7) & (hours < 21),
            0.3 + 0.7 * np.maximum.reduce([_bump(hours, 8), _bump(hours, 12), _bump(hours, 17)]),
            0.15,
        )
        awake_out = ~in_bed & ~walking & ~on_floor & ~helped
        activity = np.zeros(n)
        activity[awake_out] = (p.activity * day_curve * np.exp(act_noise))[awake_out]
        activity[in_bed & ~asleep] = 0.06
        activity[walking] = 0.7
        activity[helped] = 0.35
        activity = np.minimum(activity, 1.0)

        sleep_depth = ema(asleep.astype(float), TICK_S / 900, asleep[0] if st.sleep_depth is None else st.sleep_depth)
        act_hr = ema(activity, TICK_S / 60, st.act_smooth)

        # Heart rate: personal base, sleep dip, mild circadian swing, activity, AR(1) noise.
        circadian = 2.0 * np.sin(2 * np.pi * (hours - 10) / 24)
        hr_expected = p.hr_base * (1 - p.hr_sleep_drop * sleep_depth) + circadian
        hr_noise = ar1(rng.normal(0, p.hr_noise_sd * np.sqrt(1 - p.hr_phi**2), n), p.hr_phi, st.hr_noise)
        hr = hr_expected + p.hr_activity_gain * act_hr + hr_noise
        if p.afib:
            hr += rng.normal(0, 3.0, n)
        hr = np.clip(hr, p.hr_min, p.hr_max)

        # Respiratory rate (bed sensor).
        rr_noise = ar1(rng.normal(0, 0.6 * np.sqrt(1 - 0.9**2), n), 0.9, st.rr_noise)
        rr = np.clip(p.rr_base - 1.0 * sleep_depth + 2.0 * act_hr + rr_noise, 11, 22)

        # SpO2 every 5 min (with rare brief dips), skin temp every minute, HRV every 5 min.
        spo2 = np.full(n, np.nan)
        s_idx = np.flatnonzero((t - p.phase_s) % SPO2_PERIOD_S == 0)
        dips = np.where(rng.random(len(s_idx)) < 0.03, rng.uniform(1, 3, len(s_idx)), 0.0)
        spo2[s_idx] = np.clip(
            p.spo2_base - 0.6 * sleep_depth[s_idx] + rng.normal(0, 0.7, len(s_idx)) - dips, p.spo2_lo, p.spo2_hi
        )

        skin_noise = ar1(rng.normal(0, 0.12 * np.sqrt(1 - 0.995**2), n), 0.995, st.skin_noise)
        skin = np.full(n, np.nan)
        k_idx = np.flatnonzero((t - p.phase_s) % SKIN_PERIOD_S == 0)
        skin[k_idx] = (p.skin_base + 0.5 * sleep_depth + 0.3 * act_hr + skin_noise)[k_idx]

        hrv = np.full(n, np.nan)
        v_idx = np.flatnonzero((t - p.phase_s - HRV_PERIOD_S // 2) % HRV_PERIOD_S == 0)
        if p.afib:
            hrv[v_idx] = p.hrv_base + rng.normal(0, 15, len(v_idx))
        else:
            hrv[v_idx] = (p.hrv_base * (1 + 0.25 * sleep_depth) * (1 - 0.35 * act_hr))[v_idx] + rng.normal(
                0, 0.1 * p.hrv_base, len(v_idx)
            )
        hrv = np.maximum(hrv, 5.0)

        # Wrist motion and bed movement.
        micro = np.abs(rng.normal(0, 0.25, n))
        motion = MOTION_PER_ACTIVITY * activity + micro * np.where(asleep, night_mult, 1.0) + wrist_spike
        motion[on_floor] = np.abs(rng.normal(0, 0.15, int(on_floor.sum())))
        bed_motion = np.where(
            asleep,
            np.abs(rng.normal(0, 0.3, n)) * night_mult + bed_spike,
            3.0 + np.abs(rng.normal(0, 2.0, n)) + bed_spike,
        )

        # Rare bumps while up and about (sitting down hard); falls come from the plan.
        bumps = awake_out & (rng.random(n) < 1.5 / (12 * 720)) & np.isnan(impact)
        impact[bumps] = rng.uniform(1.3, 2.3, int(bumps.sum()))

        cols = {
            "hr": hr,
            "hrv_rmssd": hrv,
            "skin_temp": skin,
            "spo2": spo2,
            "motion": motion,
            "impact": impact,
            "worn": worn,
            "rr": rr,
            "in_bed": in_bed,
            "bed_motion": bed_motion,
        }
        for sc in self.scenarios:
            # A scenario only ever sees (and changes) the ticks inside its own [start, end) window.
            span = _span(t0, n, sc.start, sc.end)
            if span.start == span.stop:
                continue
            sub = {k: v[span].copy() for k, v in cols.items()}
            sc.apply(Signals(t[span], p, sleep_depth[span], act_hr[span], hr_expected[span], sub, rng))
            for k, v in sub.items():
                cols[k][span] = v

        batch = self._finish(t, cols, plans, t0)
        st.hr_noise, st.rr_noise, st.skin_noise, st.act_noise = (
            hr_noise[-1],
            rr_noise[-1],
            skin_noise[-1],
            act_noise[-1],
        )
        st.sleep_depth, st.act_smooth = float(sleep_depth[-1]), float(act_hr[-1])
        return batch

    def _finish(self, t: np.ndarray, cols: dict[str, np.ndarray], plans: list[DayPlan], t0: int) -> SimBatch:
        """Apply device masks, round to device resolution, and collect events."""
        rid, n = self.params.resident_id, len(t)
        worn = cols["worn"].astype(bool)
        in_bed = cols["in_bed"].astype(bool)
        off = ~worn

        def masked(key: str, decimals: int, mask: np.ndarray) -> np.ndarray:
            out = np.round(cols[key], decimals)
            out[mask] = np.nan
            return out.astype(np.float32)

        out = {
            "ts": t * 1_000_000,
            "resident_id": np.full(n, rid, dtype=np.int32),
            "hr": masked("hr", 0, off),
            "hrv_rmssd": masked("hrv_rmssd", 1, off),
            "skin_temp": masked("skin_temp", 2, off),
            "spo2": masked("spo2", 0, off),
            "motion": masked("motion", 1, off),
            "impact": masked("impact", 2, off),
            "worn": worn.astype(np.int8),
            "rr": masked("rr", 1, ~in_bed),
            "in_bed": in_bed.astype(np.int8),
            "bed_motion": np.where(in_bed, np.round(cols["bed_motion"], 1), 0.0).astype(np.float32),
            "source": np.full(n, SOURCE_SIM, dtype=np.int16),
        }

        events: list[SimEvent] = []
        prev = np.concatenate(([in_bed[0] if self.state.last_in_bed is None else self.state.last_in_bed], in_bed[:-1]))
        for i in np.flatnonzero(in_bed != prev):
            kind = "bed_return" if in_bed[i] else "bed_exit"
            events.append(SimEvent(float(t[i]), rid, kind, None, {"device": "bed_sensor"}))
        self.state.last_in_bed = bool(in_bed[-1])
        # Staff events belong to the first tick at or after them: (t0 - TICK_S, last tick]. Consecutive
        # steps tile time exactly, and an event is never emitted before its tick has happened.
        lo, hi = t0 - TICK_S, float(t[-1])
        for plan in plans:
            events.extend(e for e in plan.events if lo < e.ts <= hi)
        events.sort(key=lambda e: e.ts)
        return SimBatch(out, events)
