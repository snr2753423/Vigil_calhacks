"""Sunrise Gardens constants shared by the simulator: units, staff, round times, local-time helpers (SPEC §2)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

FACILITY_NAME = "Sunrise Gardens Skilled Nursing Facility"
UNITS = ("East", "West", "Memory Care")
UNIT_ROOM_BASE = {"East": 101, "West": 201, "Memory Care": 301}
RESIDENTS_PER_UNIT = 40
RESIDENT_IDS = range(1, 121)
LIVE_RESIDENT_ID = 120

TICK_S = 5
NIGHT_ROUND_HOURS = (22, 0, 2, 4, 6)
ROUND_SPREAD_MIN = 40  # a round walks the unit's 40 rooms from round−20 min to round+20 min


@dataclass(frozen=True)
class Staff:
    staff_id: int
    name: str
    role: str  # RN | LPN | CNA
    shift: str  # day | evening | night
    unit: str | None = None  # CNAs only


# One RN, one LPN and one CNA per unit on every shift (night = SPEC's 1 RN, 1 LPN, 3 CNAs).
STAFF: tuple[Staff, ...] = (
    Staff(1, "Grace Okafor", "RN", "night"),
    Staff(2, "Marcus Bell", "LPN", "night"),
    Staff(3, "Ana Reyes", "CNA", "night", "East"),
    Staff(4, "Kevin Tran", "CNA", "night", "West"),
    Staff(5, "Joy Mensah", "CNA", "night", "Memory Care"),
    Staff(6, "Linda Park", "RN", "day"),
    Staff(7, "Daniel Cruz", "LPN", "day"),
    Staff(8, "Tasha Greene", "CNA", "day", "East"),
    Staff(9, "Omar Haddad", "CNA", "day", "West"),
    Staff(10, "Beth Novak", "CNA", "day", "Memory Care"),
    Staff(11, "Samuel Adeyemi", "RN", "evening"),
    Staff(12, "Rachel Kim-Ford", "LPN", "evening"),
    Staff(13, "Luis Romero", "CNA", "evening", "East"),
    Staff(14, "Priya Desai", "CNA", "evening", "West"),
    Staff(15, "Hannah Brooks", "CNA", "evening", "Memory Care"),
)


def shift_at(local_hour: float) -> str:
    """Day 07:00–15:00, Evening 15:00–23:00, Night 23:00–07:00 (facility local time)."""
    h = local_hour % 24
    if 7 <= h < 15:
        return "day"
    if 15 <= h < 23:
        return "evening"
    return "night"


_STAFF_INDEX = {(s.role, s.shift, s.unit): s.staff_id for s in STAFF}


def staff_on_duty(role: str, local_hour: float, unit: str | None = None) -> int:
    return _STAFF_INDEX[(role, shift_at(local_hour), unit if role == "CNA" else None)]


# --------------------------------------------------------------------------- time helpers


def tzinfo(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def local_epoch(day: date, hours: float, tz: ZoneInfo) -> float:
    """Epoch seconds of `hours` after local midnight of `day` (wall-clock; 30.5 = next day 06:30)."""
    return (datetime.combine(day, time(0), tzinfo=tz) + timedelta(hours=hours)).timestamp()


def utc_offsets(t: np.ndarray, tz: ZoneInfo) -> np.ndarray:
    """UTC offset in seconds for every epoch second in `t` (offsets only change on hour boundaries)."""
    t = np.asarray(t, dtype=np.int64)
    if t.size == 0:
        return np.zeros(0, dtype=np.int64)
    first = (int(t.min()) // 3600) * 3600
    hours = np.arange(first, int(t.max()) + 3600, 3600)
    offs = np.array([datetime.fromtimestamp(int(h), tz).utcoffset().total_seconds() for h in hours], dtype=np.int64)
    return offs[(t - first) // 3600]


def local_hours(t: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """Local hour of day as a float in [0, 24)."""
    return ((np.asarray(t, dtype=np.int64) + offsets) % 86400) / 3600.0


EPOCH_DATE = date(1970, 1, 1)


def window_dates(t: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """Day number (days since 1970-01-01) of each tick's noon-to-noon window.

    A resident's day plan runs from local noon to the next local noon, so one plan holds a whole night.
    """
    return (np.asarray(t, dtype=np.int64) + offsets - 43200) // 86400


def day_from_number(n: int) -> date:
    return EPOCH_DATE + timedelta(days=int(n))


def scripted_night_date(now: datetime, tz: ZoneInfo) -> date:
    """Evening date of 'last night': the latest 22:00–06:30 night that has fully ended by `now`."""
    local = now.astimezone(tz)
    if local.time() >= time(6, 30):
        return local.date() - timedelta(days=1)
    return local.date() - timedelta(days=2)


def utc_iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, UTC).isoformat()
