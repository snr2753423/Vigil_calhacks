"""Per-resident inputs to the fast tier, as of one `as_of` (SPEC §7a).

`tiger.load_scoring_rows()` fetches them for all residents in a few batched queries; `build_features()`
turns those rows into one `ResidentFeatures` per resident. Everything downstream (rules, NEWS2,
priority) is pure and works on these dataclasses, so it is unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.scoring.baseline import VITAL_METRICS


@dataclass(frozen=True)
class Window:
    """Readings in a trailing window: count, range, and how much time they cover."""

    n: int = 0
    lo: float | None = None
    hi: float | None = None
    span_s: float = 0.0


@dataclass(frozen=True)
class Profile:
    resident_id: int
    name: str = ""
    sex: str = "F"
    room: str = ""
    unit: str = ""
    mobility: str = "independent"
    morse: int = 0
    cognition: str = "intact"
    code_status: str = "FULL"
    flags: tuple[str, ...] = ()
    is_live: bool = False

    @property
    def copd(self) -> bool:
        return "copd" in self.flags

    @property
    def fall_precautions(self) -> bool:
        """Fall-prevention set (SPEC §7a): mobility ≠ independent or Morse ≥ 45."""
        return self.mobility != "independent" or self.morse >= 45

    @property
    def pronoun(self) -> str:
        return "her" if self.sex == "F" else "his"


@dataclass(frozen=True)
class Note:
    ts: datetime
    text: str
    author_role: str | None = None
    routine: bool = False


@dataclass
class ResidentFeatures:
    resident_id: int
    as_of: datetime
    # ---- raw vitals, trailing 15 min
    last_ts: datetime | None = None  # newest row of any kind (device heartbeat)
    rows_15m: int = 0
    heartbeat_live: bool | None = None  # heartbeat_agg live_at(as_of), 5-min liveness
    worn: bool | None = None  # latest
    in_bed: bool | None = None  # latest
    last_worn_ts: datetime | None = None  # newest row with worn = true
    last_hr_ts: datetime | None = None  # newest non-null HR
    hr_5m: float | None = None  # 5-minute averages for NEWS2
    rr_5m: float | None = None
    spo2_recent: tuple[float, ...] = ()  # newest first, up to 2 readings in 15 min
    hr_60s: Window = field(default_factory=Window)  # worn HR readings, trailing 65 s
    rr_120s: Window = field(default_factory=Window)  # in-bed RR readings, trailing 125 s
    # ---- suspected fall: newest impact ≥ threshold within 2 h
    impact_ts: datetime | None = None
    impact_g: float | None = None
    motion_after_impact: float | None = None  # max wrist motion in the 30 s after it
    rows_after_impact: int = 0
    back_in_bed_since_impact: bool = False
    # ---- 1-minute aggregates (closed minutes only)
    # per metric (+ "in_bed" fraction): (as_of − 15 min, as_of] and (as_of − 30 min, as_of − 15 min]
    w0: dict[str, float | None] = field(default_factory=dict)
    w1: dict[str, float | None] = field(default_factory=dict)
    in_bed_frac_15m: float | None = None
    still_minutes: float | None = None  # minutes since the last minute with wrist motion (6 h lookback)
    hr_spark: list[float | None] = field(default_factory=list)  # 2 h of 5-min HR averages
    # ---- events
    last_check_ts: datetime | None = None
    last_exit_ts: datetime | None = None
    last_return_ts: datetime | None = None
    exits_tonight: int = 0
    spot_sbp: float | None = None
    spot_temp: float | None = None
    spot_ts: datetime | None = None
    note: Note | None = None  # latest note within 12 h
    note_extract: dict[str, Any] | None = None  # Snowflake AI_EXTRACT result (ai_insights) for the latest note
    # ---- Snowflake ANOMALY_DETECTION (ai_insights kind = anomaly): newest first, at most the last 2 buckets
    anomaly: list[dict[str, Any]] = field(default_factory=list)  # {"as_of", "payload"}


def build_features(as_of: datetime, rows: dict[str, list[dict]], resident_ids) -> dict[int, ResidentFeatures]:
    """Combine the per-query row dicts from `tiger.load_scoring_rows()` into features per resident."""
    feats = {rid: ResidentFeatures(rid, as_of) for rid in resident_ids}

    for r in rows["raw"]:
        f = feats.get(r["resident_id"])
        if f is None:
            continue
        f.last_ts, f.rows_15m, f.heartbeat_live = r["last_ts"], r["n"], r["heartbeat_live"]
        f.worn, f.in_bed = r["worn"], r["in_bed"]
        f.last_worn_ts, f.last_hr_ts = r["last_worn_ts"], r["last_hr_ts"]
        f.hr_5m, f.rr_5m = r["hr_5m"], r["rr_5m"]
        f.spo2_recent = tuple(float(x) for x in (r["spo2_recent"] or []) if x is not None)
        f.hr_60s = Window(r["hr60_n"] or 0, r["hr60_lo"], r["hr60_hi"], r["hr60_span"] or 0.0)
        f.rr_120s = Window(r["rr120_n"] or 0, r["rr120_lo"], r["rr120_hi"], r["rr120_span"] or 0.0)

    for r in rows["impact"]:
        f = feats.get(r["resident_id"])
        if f is None:
            continue
        f.impact_ts, f.impact_g = r["ts"], r["impact"]
        f.motion_after_impact, f.rows_after_impact = r["motion_after"], r["rows_after"] or 0
        f.back_in_bed_since_impact = bool(r["back_in_bed"])

    for r in rows["minute"]:
        f = feats.get(r["resident_id"])
        if f is None:
            continue
        f.w0 = {m: r[f"{m}_w0"] for m in VITAL_METRICS} | {"in_bed": r["in_bed_w0"]}
        f.w1 = {m: r[f"{m}_w1"] for m in VITAL_METRICS} | {"in_bed": r["in_bed_w1"]}
        f.in_bed_frac_15m = r["in_bed_w0"]
        f.still_minutes = r["still_minutes"]
        f.hr_spark = list(r["hr_spark"] or [])

    for r in rows["events"]:
        f = feats.get(r["resident_id"])
        if f is None:
            continue
        f.last_check_ts, f.last_exit_ts, f.last_return_ts = r["last_check"], r["last_exit"], r["last_return"]
        f.exits_tonight = r["exits_tonight"] or 0
        if spot := r["spot"]:
            f.spot_sbp, f.spot_temp, f.spot_ts = spot.get("sbp"), spot.get("temp_c"), r["spot_ts"]
        if note := r["note"]:
            f.note = Note(r["note_ts"], note.get("text", ""), note.get("author_role"), bool(note.get("routine")))

    for r in rows["note_extract"]:
        f = feats.get(r["resident_id"])
        if f is not None and f.note is not None and r["as_of"] >= f.note.ts:
            f.note_extract = r["payload"]

    for r in rows.get("anomaly", ()):  # ordered newest first per resident
        f = feats.get(r["resident_id"])
        if f is not None:
            f.anomaly.append({"as_of": r["as_of"], "payload": r["payload"]})
    return feats
