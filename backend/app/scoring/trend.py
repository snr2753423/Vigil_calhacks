"""P_trend from a Snowflake FORECAST (SPEC §7d, §9 ③). Pure: unit-tested without a database.

A forecast (`deep/forecast.py`) predicts RR, SpO₂ and HR in 15-min buckets. For each metric we find the
first forecast bucket that enters a NEWS2 3-point zone the resident isn't already in:

    RR ≥ 25 or ≤ 8 · SpO₂ ≤ 91 (scale 2 / COPD: ≤ 83) · HR ≥ 131 or ≤ 40

    P_trend = 1 if that crossing is within 2 h of as_of, 0.5 within 4 h, else 0   (max over metrics)

A crossing the forecast placed before as_of still counts as 1 (it was predicted to be there by now). The crossing
time is the end of the first bucket in the zone ("RR forecast to cross 25 by ~03:00").

A threshold within MIN_GAP (RR 1/min, SpO₂ 2 %, HR 10 bpm) of the last observed value is not a trend: NEWS2 already scores where the resident is, and
a forecast drifting 1 % across it is noise. (Measured: Lee's 15-min SpO₂ minimum sat at 91–92 % all night, so every
forecast "fell to 91 %" and P_trend was pinned at 1, while his RR climbing toward 25 is the real trend.)

Crossings are computed when read (`annotate`, `p_trend`), from the stored points, so a stored forecast follows the
current rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FULL_WITHIN = timedelta(hours=2)
HALF_WITHIN = timedelta(hours=4)
BUCKET = timedelta(minutes=15)

LABEL = {"rr": "RR", "spo2": "SpO₂", "hr": "HR"}
# threshold this far from the last value, at least, to be a trend. SpO₂ is the bucket's noisy minimum; RR is a smooth
# bed-sensor average, so 1/min is real (Lee at 23.4 heading for 25, Patel at 9 heading for 8).
MIN_GAP = {"rr": 1.0, "spo2": 2.0, "hr": 10.0}


@dataclass(frozen=True)
class Zone:
    """A NEWS2 3-point zone: value ≥ threshold (direction 'up') or ≤ threshold ('down')."""

    threshold: float
    direction: str  # up | down

    def contains(self, v: float | None) -> bool:
        if v is None:
            return False
        return v >= self.threshold if self.direction == "up" else v <= self.threshold


def zones(metric: str, copd: bool = False) -> list[Zone]:
    if metric == "rr":
        return [Zone(25, "up"), Zone(8, "down")]
    if metric == "spo2":
        return [Zone(83 if copd else 91, "down")]
    if metric == "hr":
        return [Zone(131, "up"), Zone(40, "down")]
    return []


def crossing(metric: str, points: list[list], last_value: float | None, copd: bool = False) -> dict | None:
    """The first forecast bucket [ts_iso, forecast, lower, upper] inside a 3-point zone the last observed value
    isn't already in. → {threshold, direction, bucket, crosses_at (bucket end, ISO)} or None."""
    gap = MIN_GAP.get(metric, 0.0)
    for z in zones(metric, copd):
        if z.contains(last_value):
            continue  # already scored by NEWS2 itself; not a trend
        if last_value is not None and abs(z.threshold - last_value) < gap:
            continue  # within noise of where the resident already is
        for p in points:
            if z.contains(p[1]):
                start = datetime.fromisoformat(p[0])
                return {"threshold": z.threshold, "direction": z.direction, "bucket": p[0],
                        "crosses_at": (start + BUCKET).isoformat()}  # fmt: skip
    return None


def p_trend(payload: dict | None, as_of: datetime, copd: bool | None = None) -> tuple[float, str | None, dict | None]:
    """(P_trend, metric, its crossing) from one stored forecast payload, as of `as_of`. `copd` defaults to the flag
    stored with the forecast."""
    if not payload:
        return 0.0, None, None
    copd = bool(payload.get("copd")) if copd is None else copd
    best: tuple[float, str | None, dict | None] = (0.0, None, None)
    for metric, m in (payload.get("metrics") or {}).items():
        c = crossing(metric, m.get("points") or [], m.get("last_value"), copd) if "points" in m else m.get("crossing")
        if not c:
            continue
        lead = datetime.fromisoformat(c["crosses_at"]) - as_of
        p = 1.0 if lead <= FULL_WITHIN else 0.5 if lead <= HALF_WITHIN else 0.0
        if p > best[0] or (p == best[0] and p > 0 and best[2] and c["crosses_at"] < best[2]["crosses_at"]):
            best = (p, metric, c)
    return best


def callout(metric: str, c: dict, tz: ZoneInfo) -> str:
    """'RR forecast to cross 25 by ~03:00' / 'SpO₂ forecast to fall to 91 by ~03:15'."""
    when = datetime.fromisoformat(c["crosses_at"]).astimezone(tz).strftime("%H:%M")
    verb = "cross" if c["direction"] == "up" else "fall to"
    unit = "%" if metric == "spo2" else ""
    return f"{LABEL.get(metric, metric)} forecast to {verb} {c['threshold']:g}{unit} by ~{when}"


def annotate(payload: dict, tz: ZoneInfo, copd: bool | None = None) -> dict:
    """The payload with each metric's crossing, P_trend at the time it was made, and the callout recomputed."""
    copd = bool(payload.get("copd")) if copd is None else copd
    metrics = {
        m: d | {"crossing": crossing(m, d.get("points") or [], d.get("last_value"), copd)}
        for m, d in (payload.get("metrics") or {}).items()
    }
    out = payload | {"metrics": metrics}
    p, metric, c = p_trend(out, datetime.fromisoformat(payload["as_of"]), copd)
    return out | {"p_trend": p, "callout": callout(metric, c, tz) if p and metric and c else None}
