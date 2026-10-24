"""Builders for scoring unit tests (no database)."""

from datetime import UTC, datetime, timedelta

from app.scoring.baseline import Baselines
from app.scoring.features import Profile, ResidentFeatures, Window
from app.scoring.rules import NightClock
from app.sim import facility as fac

TZ = fac.tzinfo("America/Los_Angeles")
NIGHT = datetime(2026, 10, 16, 9, 14, tzinfo=UTC)  # 02:14 facility time
DAY = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)  # 14:00 facility time


def ago(as_of: datetime, **kw) -> datetime:
    return as_of - timedelta(**kw)


def profile(rid: int = 7, **kw) -> Profile:
    base = {"name": "Test Resident", "sex": "F", "room": "107", "unit": "East", "mobility": "1-assist", "morse": 55}
    return Profile(resident_id=rid, **(base | kw))


def baselines(rid: int = 7, **behavior) -> Baselines:
    """Night block (0) and day block (3) baselines with round numbers."""
    rows = []
    for block in range(6):
        rows += [
            (rid, block, "hr", 60.0, 64.0, 70.0, 64.0, 3.0),
            (rid, block, "rr", 13.0, 15.0, 17.0, 15.0, 1.0),
            (rid, block, "spo2", 94.0, 96.0, 98.0, 96.0, 1.0),
            (rid, block, "skin_temp", 33.5, 34.0, 34.5, 34.0, 0.3),
            (rid, block, "motion", 1.0, 2.0, 4.0, 2.5, 1.0),
        ]
    rows.append((rid, -1, "night_bed_exits", 0.0, 1.0, behavior.get("exits_p95", 2.0), 1.0, 0.5))
    rows.append((rid, -1, "still_minutes", 30.0, 45.0, behavior.get("still_p95", 60.0), 45.0, 10.0))
    return Baselines(rows)


def normal_window() -> dict:
    return {"hr": 64.0, "rr": 15.0, "spo2": 96.0, "skin_temp": 34.0, "motion": 2.0, "in_bed": 1.0}


def features(as_of: datetime = NIGHT, rid: int = 7, **kw) -> ResidentFeatures:
    """A resident asleep in bed, wristband on, everything at their normal, checked 20 min ago."""
    f = ResidentFeatures(
        resident_id=rid,
        as_of=as_of,
        last_ts=ago(as_of, seconds=2),
        rows_15m=180,
        heartbeat_live=True,
        worn=True,
        in_bed=True,
        last_worn_ts=ago(as_of, seconds=2),
        last_hr_ts=ago(as_of, seconds=2),
        hr_5m=64.0,
        rr_5m=15.0,
        spo2_recent=(96.0, 96.0),
        hr_60s=Window(13, 62.0, 66.0, 60.0),
        rr_120s=Window(25, 14.0, 16.0, 120.0),
        w0=normal_window(),
        w1=normal_window(),
        in_bed_frac_15m=1.0,
        still_minutes=10.0,
        last_check_ts=ago(as_of, minutes=20),
    )
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def clock(**kw) -> NightClock:
    return NightClock(tz=TZ, **kw)
