"""Critical, fall-prevention and device-issue rules (SPEC §7a).

Pure functions over `ResidentFeatures`, all relative to `f.as_of` (never `now()`):

* critical → score 100: extreme HR, low SpO2, extreme RR, possibly unresponsive, suspected fall
* fall prevention → score 90: unassisted night-time bed exit by a resident on fall precautions
* device issue → score 30 (its own icon, never an alarm): no data > 5 min, or band off > 10 min
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.scoring.baseline import Baselines, tod_block
from app.scoring.features import Profile, ResidentFeatures

CRITICAL_SCORE = 100
FALL_PREVENTION_SCORE = 90
DEVICE_SCORE = 30

HR_LOW, HR_HIGH, HR_SUSTAIN_S = 40, 130, 60
SPO2_LOW, SPO2_LOW_COPD = 88, 84
RR_LOW, RR_HIGH, RR_SUSTAIN_S = 8, 28, 120
SUSTAIN_SLACK_S = 5  # one 5-s tick of slack when checking that readings cover the window
UNRESPONSIVE_HR_DROP = 10
STILL_P95_DEFAULT_MIN, STILL_P95_FLOOR_MIN = 60.0, 20.0
HR_LOST_AFTER = timedelta(seconds=60)
FALL_IMPACT_G = 3.0  # everyday bumps (sitting down hard) peak at 1.3–2.3 g
FALL_STILL_S = 30
FALL_STILL_MOTION = 2.0  # max wrist counts per 5-s tick that still counts as "no motion"
CHECK_BEFORE_EXIT = timedelta(minutes=5)
DEVICE_GAP = timedelta(minutes=5)
BAND_OFF = timedelta(minutes=10)
NIGHT_START_H, NIGHT_END_H = 22, 6


@dataclass(frozen=True)
class RuleHit:
    rule: str  # hr_extreme | spo2_low | rr_extreme | unresponsive | fall | fall_prevention | device_offline | band_off
    kind: str  # critical | fall_prevention | device
    score: int
    text: str
    since: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "kind": self.kind,
            "score": self.score,
            "text": self.text,
            "since": self.since.isoformat() if self.since else None,
        }


@dataclass(frozen=True)
class NightClock:
    """Night-window checks with the demo's display offset and force-night flag (SPEC §4).

    Stored data stays real UTC; only these checks (and the header clock) see the shifted time.
    """

    tz: ZoneInfo
    force_night: bool = False
    force_night_since: datetime | None = None
    offset_s: int = 0

    def display(self, ts: datetime) -> datetime:
        return (ts + timedelta(seconds=self.offset_s)).astimezone(self.tz)

    def hhmm(self, ts: datetime) -> str:
        return self.display(ts).strftime("%H:%M")

    def _forced(self, ts: datetime) -> bool:
        return self.force_night and (self.force_night_since is None or ts >= self.force_night_since)

    def is_night(self, ts: datetime) -> bool:
        h = self.display(ts).hour
        return h >= NIGHT_START_H or h < NIGHT_END_H or self._forced(ts)

    def night_start(self, as_of: datetime) -> datetime | None:
        """Real UTC start of the current night window ("bed exits tonight"), or None during the day."""
        d = self.display(as_of)
        if d.hour >= NIGHT_START_H or d.hour < NIGHT_END_H:
            day = d.date() if d.hour >= NIGHT_START_H else d.date() - timedelta(days=1)
            start = datetime(day.year, day.month, day.day, NIGHT_START_H, tzinfo=self.tz)
            return start - timedelta(seconds=self.offset_s)
        if self._forced(as_of):
            return self.force_night_since or as_of - timedelta(hours=8)
        return None


def _sustained(n: int, span_s: float, need_s: float) -> bool:
    return n >= 2 and span_s >= need_s - SUSTAIN_SLACK_S


def hr_extreme(f: ResidentFeatures) -> RuleHit | None:
    """HR < 40 or > 130 sustained ≥ 60 s while worn."""
    w = f.hr_60s
    if not _sustained(w.n, w.span_s, HR_SUSTAIN_S) or w.lo is None or w.hi is None:
        return None
    if w.hi < HR_LOW:
        return RuleHit("hr_extreme", "critical", CRITICAL_SCORE, f"HR {w.hi:.0f} or lower for 60 s+")
    if w.lo > HR_HIGH:
        return RuleHit("hr_extreme", "critical", CRITICAL_SCORE, f"HR {w.lo:.0f} or higher for 60 s+")
    return None


def spo2_low(f: ResidentFeatures, p: Profile) -> RuleHit | None:
    """SpO2 ≤ 88 (≤ 84 if COPD) on 2 consecutive readings."""
    limit = SPO2_LOW_COPD if p.copd else SPO2_LOW
    last2 = f.spo2_recent[:2]
    if len(last2) == 2 and all(v <= limit for v in last2):
        return RuleHit("spo2_low", "critical", CRITICAL_SCORE, f"SpO₂ {last2[0]:.0f}% on 2 readings in a row")
    return None


def rr_extreme(f: ResidentFeatures) -> RuleHit | None:
    """RR ≤ 8 or ≥ 28 sustained ≥ 2 min while in bed."""
    w = f.rr_120s
    if not _sustained(w.n, w.span_s, RR_SUSTAIN_S) or w.lo is None or w.hi is None:
        return None
    if w.hi <= RR_LOW:
        return RuleHit("rr_extreme", "critical", CRITICAL_SCORE, f"RR {w.hi:.0f}/min or lower for 2 min+")
    if w.lo >= RR_HIGH:
        return RuleHit("rr_extreme", "critical", CRITICAL_SCORE, f"RR {w.lo:.0f}/min or higher for 2 min+")
    return None


def unresponsive(f: ResidentFeatures, p: Profile, baselines: Baselines, tz: ZoneInfo) -> RuleHit | None:
    """Worn AND no motion for > 2× the resident's still_minutes_p95 AND (HR < their p05 − 10 OR HR lost)."""
    if f.worn is not True or f.still_minutes is None:
        return None
    p95 = baselines.behavior_for(p.resident_id).still_minutes_p95 or STILL_P95_DEFAULT_MIN
    p95 = max(p95, STILL_P95_FLOOR_MIN)
    if f.still_minutes <= 2 * p95:
        return None
    # HR lost *while worn*: the band is still reporting but without HR. No data at all is a device/data
    # gap (device issue), not a sign about the resident.
    reporting = f.last_ts is not None and f.as_of - f.last_ts <= HR_LOST_AFTER
    hr_lost = reporting and (f.last_hr_ts is None or f.as_of - f.last_hr_ts > HR_LOST_AFTER)
    stats = baselines.get(p.resident_id, tod_block(f.as_of, tz), "hr")
    hr_low = f.hr_5m is not None and stats is not None and f.hr_5m < stats.p05 - UNRESPONSIVE_HR_DROP
    if not (hr_lost or hr_low):
        return None
    still = f"No movement for {f.still_minutes:.0f} min (usual ≤ {p95:.0f})"
    hr = "heart-rate signal lost" if hr_lost else f"HR {f.hr_5m:.0f} (usual ≥ {stats.p05:.0f})"
    return RuleHit("unresponsive", "critical", CRITICAL_SCORE, f"Possibly unresponsive: {still}, {hr}")


def suspected_fall(f: ResidentFeatures, clock: NightClock) -> RuleHit | None:
    """Impact spike, then ≥ 30 s no motion, not in bed. Stays active until a check is logged or they are
    back in bed."""
    if f.impact_ts is None or f.impact_g is None or f.impact_g < FALL_IMPACT_G:
        return None
    if (f.as_of - f.impact_ts).total_seconds() < FALL_STILL_S or f.rows_after_impact < 2:
        return None
    if f.motion_after_impact is None or f.motion_after_impact >= FALL_STILL_MOTION:
        return None
    if f.in_bed is True or f.back_in_bed_since_impact:
        return None
    if f.last_check_ts is not None and f.last_check_ts > f.impact_ts:
        return None
    return RuleHit(
        "fall",
        "critical",
        CRITICAL_SCORE,
        f"Suspected fall at {clock.hhmm(f.impact_ts)}: {f.impact_g:.1f} g impact, then no movement",
        f.impact_ts,
    )


def fall_prevention(f: ResidentFeatures, p: Profile, clock: NightClock) -> RuleHit | None:
    """Night-time bed exit by a resident on fall precautions, unless a check was logged within 5 min of
    getting up (an answered call light) or since."""
    if not p.fall_precautions or f.last_exit_ts is None or f.in_bed is True:
        return None
    if f.last_return_ts is not None and f.last_return_ts >= f.last_exit_ts:
        return None
    if not clock.is_night(f.last_exit_ts):
        return None
    if f.last_check_ts is not None and f.last_check_ts >= f.last_exit_ts - CHECK_BEFORE_EXIT:
        return None
    needs = p.mobility if p.mobility != "independent" else "fall risk"
    return RuleHit(
        "fall_prevention",
        "fall_prevention",
        FALL_PREVENTION_SCORE,
        f"Out of bed since {clock.hhmm(f.last_exit_ts)} without staff ({needs}, Morse {p.morse})",
        f.last_exit_ts,
    )


def device_issue(f: ResidentFeatures) -> RuleHit | None:
    """No device data for > 5 min (heartbeat_agg liveness), or the band is off for > 10 min."""
    offline = f.last_ts is None or f.heartbeat_live is False or f.as_of - f.last_ts > DEVICE_GAP
    if offline:
        if f.last_ts is None:
            text = "No device data in 15+ min"
        else:
            text = f"No device data for {(f.as_of - f.last_ts).total_seconds() / 60:.0f} min"
        return RuleHit("device_offline", "device", DEVICE_SCORE, text, f.last_ts)
    if f.worn is False and (f.last_worn_ts is None or f.as_of - f.last_worn_ts > BAND_OFF):
        mins = "15+" if f.last_worn_ts is None else f"{(f.as_of - f.last_worn_ts).total_seconds() / 60:.0f}"
        return RuleHit("band_off", "device", DEVICE_SCORE, f"Wristband off for {mins} min", f.last_worn_ts)
    return None


def evaluate_rules(
    f: ResidentFeatures, p: Profile, baselines: Baselines, clock: NightClock
) -> tuple[list[RuleHit], RuleHit | None]:
    """(critical + fall-prevention hits, device issue). Critical hits come first."""
    hits = [
        h
        for h in (
            suspected_fall(f, clock),
            hr_extreme(f),
            spo2_low(f, p),
            rr_extreme(f),
            unresponsive(f, p, baselines, clock.tz),
            fall_prevention(f, p, clock),
        )
        if h is not None
    ]
    return hits, device_issue(f)
