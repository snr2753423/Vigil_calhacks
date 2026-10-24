"""NEWS2 subset incl. COPD SpO2 Scale 2 (SPEC §7b).

Standard RCP NEWS2 bands, computed from whatever inputs are available: a missing parameter is left out
of the total (partial score) and listed in `missing`. Observations are rounded to the chart's resolution
first (whole numbers; temperature to 0.1 °C), so a 5-minute average of 20.4 breaths/min scores like 20.
Skin temperature is never used: the wrist is not core temperature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

PARAMS = ("rr", "spo2", "hr", "sbp", "temp", "consciousness")


def _round(value: float, places: int = 0) -> float:
    """Round half up (Python's round() is banker's rounding: 20.5 → 20)."""
    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def score_rr(rr: float) -> int:
    v = _round(rr)
    if v <= 8:
        return 3
    if v <= 11:
        return 1
    if v <= 20:
        return 0
    if v <= 24:
        return 2
    return 3


def score_spo2_scale1(spo2: float) -> int:
    v = _round(spo2)
    if v <= 91:
        return 3
    if v <= 93:
        return 2
    if v <= 95:
        return 1
    return 0


def score_spo2_scale2(spo2: float, on_oxygen: bool = False) -> int:
    """Hypercapnic respiratory failure (COPD) scale: target 88–92 %."""
    v = _round(spo2)
    if v <= 83:
        return 3
    if v <= 85:
        return 2
    if v <= 87:
        return 1
    if v <= 92 or not on_oxygen:  # 88–92, or ≥ 93 on room air
        return 0
    if v <= 94:
        return 1
    if v <= 96:
        return 2
    return 3


def score_hr(hr: float) -> int:
    v = _round(hr)
    if v <= 40:
        return 3
    if v <= 50:
        return 1
    if v <= 90:
        return 0
    if v <= 110:
        return 1
    if v <= 130:
        return 2
    return 3


def score_sbp(sbp: float) -> int:
    v = _round(sbp)
    if v <= 90:
        return 3
    if v <= 100:
        return 2
    if v <= 110:
        return 1
    if v <= 219:
        return 0
    return 3


def score_temp(temp_c: float) -> int:
    v = _round(temp_c, 1)
    if v <= 35.0:
        return 3
    if v <= 36.0:
        return 1
    if v <= 38.0:
        return 0
    if v <= 39.0:
        return 1
    return 2


def score_consciousness(new_confusion: bool) -> int:
    """ACVPU: new confusion scores 3, alert scores 0."""
    return 3 if new_confusion else 0


@dataclass(frozen=True)
class News2Inputs:
    rr: float | None = None
    spo2: float | None = None
    hr: float | None = None
    sbp: float | None = None  # latest spot check within 12 h
    temp: float | None = None  # core temperature, latest spot check within 12 h
    new_confusion: bool | None = None  # latest note extraction within 12 h (None = no note)
    copd: bool = False  # SpO2 Scale 2
    on_oxygen: bool = False


@dataclass(frozen=True)
class News2Param:
    value: float | bool
    points: int


@dataclass(frozen=True)
class News2Result:
    total: int
    params: dict[str, News2Param] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    spo2_scale: int = 1

    @property
    def any_three(self) -> bool:
        return any(p.points == 3 for p in self.params.values())

    @property
    def forces_high(self) -> bool:
        """Any single parameter = 3 or total ≥ 5 forces at least HIGH (SPEC §7b)."""
        return self.any_three or self.total >= 5

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "params": {k: {"value": p.value, "points": p.points} for k, p in self.params.items()},
            "missing": list(self.missing),
            "spo2_scale": self.spo2_scale,
            "forces_high": self.forces_high,
        }


def compute(inputs: News2Inputs) -> News2Result:
    params: dict[str, News2Param] = {}
    missing: list[str] = []

    def add(name: str, value, scorer) -> None:
        if value is None:
            missing.append(name)
        else:
            params[name] = News2Param(value, scorer(value))

    add("rr", inputs.rr, score_rr)
    if inputs.copd:
        add("spo2", inputs.spo2, lambda v: score_spo2_scale2(v, inputs.on_oxygen))
    else:
        add("spo2", inputs.spo2, score_spo2_scale1)
    add("hr", inputs.hr, score_hr)
    add("sbp", inputs.sbp, score_sbp)
    add("temp", inputs.temp, score_temp)
    add("consciousness", inputs.new_confusion, score_consciousness)
    total = sum(p.points for p in params.values())
    return News2Result(total, params, tuple(missing), 2 if inputs.copd else 1)
