"""Z-scores vs resident_baselines by 4-hour block (SPEC §7a).

z = (value − p50) / ((p95 − p05) / 3.29): for a normal distribution p05..p95 spans 3.29 σ, so the
denominator is a robust σ. A per-metric floor keeps a very steady resident's narrow band from turning
sensor noise into huge z. Until Snowflake anomaly detection lands (Phase 5) the anomaly severity is the
Tiger fallback min(|z| / 4, 1) (SPEC §7d), applied in the direction that matters clinically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

Z_SPREAD = 3.29
ANOMALOUS_Z = 2.0  # |z| at which a metric counts toward the multi-signal pattern

VITAL_METRICS = ("hr", "rr", "spo2", "skin_temp", "motion")

# Smallest σ used per metric, so a 1-bpm-wide personal band can't make a 3-bpm wobble look extreme.
MIN_SCALE = {"hr": 2.0, "rr": 0.75, "spo2": 0.75, "skin_temp": 0.15, "motion": 1.0}

# Which side of normal is a concern: +1 high only, -1 low only, 0 both.
DIRECTION = {"hr": 0, "rr": 0, "spo2": -1, "skin_temp": 0, "motion": +1}

BLOCK_NAMES = {0: "night", 1: "night", 2: "morning", 3: "afternoon", 4: "evening", 5: "night"}


def tod_block(as_of: datetime, tz: ZoneInfo) -> int:
    """0–5: 4-hour block of facility local time (0 = 00:00–04:00), same as the baseline job."""
    return as_of.astimezone(tz).hour // 4


@dataclass(frozen=True)
class Stats:
    p05: float
    p50: float
    p95: float
    mean: float | None = None
    stddev: float | None = None

    def scale(self, metric: str) -> float:
        return max((self.p95 - self.p05) / Z_SPREAD, MIN_SCALE.get(metric, 1e-6))


def zscore(value: float, stats: Stats, metric: str) -> float:
    return (value - stats.p50) / stats.scale(metric)


def severity(z: float, metric: str) -> float:
    """Tiger fallback anomaly severity in [0, 1]: min(|z| / 4, 1), one-sided where only one side matters."""
    d = DIRECTION.get(metric, 0)
    if d > 0:
        z = max(z, 0.0)
    elif d < 0:
        z = min(z, 0.0)
    return min(abs(z) / 4.0, 1.0)


@dataclass(frozen=True)
class Behavior:
    night_bed_exits_p95: float | None = None
    still_minutes_p95: float | None = None


class Baselines:
    """In-memory copy of resident_baselines (the job refreshes it hourly; the evaluator reloads every few min)."""

    def __init__(self, rows: list[tuple] | None = None):
        self.vitals: dict[tuple[int, int, str], Stats] = {}
        self.behavior: dict[int, Behavior] = {}
        for row in rows or []:
            self._add(*row)

    def _add(self, rid: int, block: int, metric: str, p05, p50, p95, mean, stddev) -> None:
        if block == -1:
            b = self.behavior.get(rid, Behavior())
            if metric == "night_bed_exits":
                b = Behavior(p95, b.still_minutes_p95)
            elif metric == "still_minutes":
                b = Behavior(b.night_bed_exits_p95, p95)
            self.behavior[rid] = b
        elif None not in (p05, p50, p95):
            self.vitals[(rid, block, metric)] = Stats(float(p05), float(p50), float(p95), mean, stddev)

    def get(self, rid: int, block: int, metric: str) -> Stats | None:
        """Stats for this block; falls back to the nearest block that has the metric (e.g. RR is only
        measured in bed, so daytime blocks may have no RR baseline)."""
        if (s := self.vitals.get((rid, block, metric))) is not None:
            return s
        for d in (1, -1, 2, -2, 3):
            if (s := self.vitals.get((rid, (block + d) % 6, metric))) is not None:
                return s
        return None

    def behavior_for(self, rid: int) -> Behavior:
        return self.behavior.get(rid, Behavior())

    def block_stats(self, rid: int) -> dict[str, dict[int, dict[str, float]]]:
        """{metric: {block: {p05, p50, p95}}} for one resident (chart baseline bands)."""
        out: dict[str, dict[int, dict[str, float]]] = {}
        for (r, block, metric), s in self.vitals.items():
            if r == rid:
                out.setdefault(metric, {})[block] = {"p05": s.p05, "p50": s.p50, "p95": s.p95}
        return out
