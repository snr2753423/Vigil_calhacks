"""Priority score, tiers and deterministic top-3 factors (SPEC §7d).

    score = 100 × (0.30·P_news + 0.25·P_anom + 0.15·P_trend + 0.10·P_behav + 0.12·P_concern + 0.08·P_overdue)
            + 10 if ≥ 3 metrics are anomalous together, capped at 100
    critical rule → 100 · fall prevention → 90 · device issue only → 30 · NEWS2 forcing HIGH → ≥ 60

Factor strings are built only from the computed components (never by AI), e.g. "HR +22% vs her night
normal", "4 bed exits tonight (usual ≤ 2)", "New confusion (note 21:30)".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal
from zoneinfo import ZoneInfo

from app.scoring import news2
from app.scoring.baseline import (
    ANOMALOUS_Z,
    BLOCK_NAMES,
    DIRECTION,
    VITAL_METRICS,
    Baselines,
    Stats,
    severity,
    tod_block,
    zscore,
)
from app.scoring.concern import NoteConcern, note_concern
from app.scoring.features import Profile, ResidentFeatures
from app.scoring.rules import NightClock, RuleHit, evaluate_rules

Tier = Literal["CRITICAL", "HIGH", "WATCH", "STABLE"]
TIERS: tuple[Tier, ...] = ("STABLE", "WATCH", "HIGH", "CRITICAL")  # ascending
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
TIER_ENTER = {"CRITICAL": 80.0, "HIGH": 60.0, "WATCH": 35.0, "STABLE": 0.0}

WEIGHTS = {"news": 0.30, "anom": 0.25, "trend": 0.15, "behav": 0.10, "concern": 0.12, "overdue": 0.08}
MULTI_SIGNAL_BONUS = 10.0
MULTI_SIGNAL_MIN = 3
NEWS2_FULL = 7.0
NEWS2_HIGH_FLOOR = 60.0
OVERDUE_FULL_MIN = 120.0
RESTLESS_FULL_RATIO = 5.0  # in-bed wrist movement this many × the usual median → restlessness 1
RESTLESS_MIN_IN_BED = 0.5  # wrist movement is compared with the baseline only while mostly in bed
Z_SHARE_CAP = 10.0
MIN_FACTOR_POINTS = 1.0  # a contribution under one point is not worth a chip

METRIC_LABEL = {"hr": "HR", "rr": "RR", "spo2": "SpO₂", "skin_temp": "Skin temp", "motion": "Movement"}


def tier_for_score(score: float) -> Tier:
    for t in reversed(TIERS):
        if score >= TIER_ENTER[t]:
            return t
    return "STABLE"


@dataclass
class MetricDeviation:
    """How far one metric is from this resident's normal: a Tiger z vs the personal baseline (fallback), or
    a Snowflake ANOMALY_DETECTION result (value vs the model's expected value and 99 % interval)."""

    metric: str
    value: float
    z: float
    severity: float
    p50: float  # personal median (Tiger) or the model's forecast (Snowflake)
    window: int  # 0 = last 15 min / newest bucket, 1 = the 15 min / bucket before
    lower: float | None = None  # Snowflake expected range
    upper: float | None = None
    source: str = "tiger_z"  # tiger_z | snowflake

    @property
    def anomalous(self) -> bool:
        if self.source == "snowflake":
            return self.severity > 0  # outside the model's 99 % interval, on the side that matters
        return self.severity >= ANOMALOUS_Z / 4.0

    @property
    def share(self) -> float:
        """Weight for splitting P_anom's points between metrics: how far off, in the direction that matters."""
        d = DIRECTION.get(self.metric, 0)
        z = max(self.z, 0.0) if d > 0 else min(self.z, 0.0) if d < 0 else self.z
        return min(abs(z), Z_SHARE_CAP)


@dataclass
class Components:
    p_news: float = 0.0
    p_anom: float = 0.0
    p_trend: float = 0.0
    p_behav: float = 0.0
    p_concern: float = 0.0
    p_overdue: float = 0.0
    multi_signal: bool = False
    anom_source: str = "tiger_z"  # snowflake when ANOMALY_DETECTION results are fresh, else the Tiger z fallback
    anom_as_of: str | None = None  # when the newest Snowflake result became known
    # detail behind the numbers
    news2_total: int = 0
    anomalous_metrics: list[str] = field(default_factory=list)
    bed_exits: float = 0.0
    restlessness: float = 0.0
    still: float = 0.0
    minutes_since_check: float | None = None

    @property
    def base_score(self) -> float:
        return 100.0 * (
            WEIGHTS["news"] * self.p_news
            + WEIGHTS["anom"] * self.p_anom
            + WEIGHTS["trend"] * self.p_trend
            + WEIGHTS["behav"] * self.p_behav
            + WEIGHTS["concern"] * self.p_concern
            + WEIGHTS["overdue"] * self.p_overdue
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 3)
        # The UI's "why this score" bars: points per component and their maxima, so weights live only here.
        d["points"] = {k: round(100 * w * getattr(self, f"p_{k}"), 2) for k, w in WEIGHTS.items()}
        d["max_points"] = {k: round(100 * w, 2) for k, w in WEIGHTS.items()}
        return d


@dataclass(frozen=True)
class Factor:
    key: str
    text: str
    points: float  # contribution to the score
    kind: str  # rule | vital | behavior | concern | overdue | device

    def to_dict(self) -> dict:
        return {"key": self.key, "text": self.text, "points": round(self.points, 1), "kind": self.kind}


@dataclass
class PriorityResult:
    resident_id: int
    score: float
    tier: Tier  # from the score alone (the evaluator applies hysteresis/persistence/suppression)
    components: Components
    factors: list[Factor]  # top 3
    news2: news2.News2Result
    rules: list[RuleHit]
    device: RuleHit | None
    concern: NoteConcern | None
    deviations: dict[str, MetricDeviation]

    @property
    def rule(self) -> RuleHit | None:
        return self.rules[0] if self.rules else None


def combine_formula(c: Components) -> float:
    score = c.base_score + (MULTI_SIGNAL_BONUS if c.multi_signal else 0.0)
    return min(score, 100.0)


def final_score(c: Components, rules: list[RuleHit], device: RuleHit | None, news: news2.News2Result) -> float:
    """Formula, then the floors: rules (100 / 90), NEWS2 forcing HIGH (≥ 60), device issue (≥ 30)."""
    score = combine_formula(c)
    if news.forces_high:
        score = max(score, NEWS2_HIGH_FLOOR)
    for hit in rules:
        score = max(score, float(hit.score))
    if device is not None:
        score = max(score, float(device.score))
    return round(min(score, 100.0), 1)


# --------------------------------------------------------------------------- components


def deviations(f: ResidentFeatures, rid: int, baselines: Baselines, block: int) -> dict[str, MetricDeviation]:
    """Per metric: the larger-severity of the last two 15-min windows vs the personal baseline."""
    out: dict[str, MetricDeviation] = {}
    for metric in VITAL_METRICS:
        stats = baselines.get(rid, block, metric)
        if stats is None:
            continue
        best: MetricDeviation | None = None
        for i, window in enumerate((f.w0, f.w1)):
            value = window.get(metric)
            if value is None:
                continue
            if metric == "motion" and (window.get("in_bed") or 0) < RESTLESS_MIN_IN_BED:
                continue  # walking to the bathroom is not restlessness
            z = zscore(value, stats, metric)
            dev = MetricDeviation(metric, float(value), z, severity(z, metric), stats.p50, i)
            if best is None or dev.severity > best.severity:
                best = dev
        if best is not None:
            out[metric] = best
    return out


def snowflake_deviations(results: list[dict]) -> dict[str, MetricDeviation]:
    """Per metric: the higher-severity of the last two Snowflake buckets (SPEC §7d), newest on a tie."""
    out: dict[str, MetricDeviation] = {}
    for i, r in enumerate(results[:2]):
        for metric, v in (r["payload"].get("metrics") or {}).items():
            if metric not in VITAL_METRICS or v.get("y") is None:
                continue
            forecast = v.get("forecast")
            dev = MetricDeviation(
                metric=metric,
                value=float(v["y"]),
                z=float(v.get("z") or 0.0),
                severity=float(v.get("severity") or 0.0),
                p50=float(forecast if forecast is not None else v["y"]),
                window=i,
                lower=v.get("lower"),
                upper=v.get("upper"),
                source="snowflake",
            )
            if metric not in out or dev.severity > out[metric].severity:
                out[metric] = dev
    return out


def p_anom(devs: dict[str, MetricDeviation]) -> float:
    """1 − Π(1 − severity_m) over metrics (SPEC §7d)."""
    prod = 1.0
    for d in devs.values():
        prod *= 1.0 - d.severity
    return 1.0 - prod


def restlessness(f: ResidentFeatures, motion: Stats | None) -> float:
    """In-bed wrist movement vs the usual median for this block, 0..1."""
    if motion is None or (f.in_bed_frac_15m or 0) < RESTLESS_MIN_IN_BED:
        return 0.0
    value = f.w0.get("motion")
    if value is None:
        return 0.0
    ratio = value / max(motion.p50, 0.5)
    return min(max((ratio - 1.0) / (RESTLESS_FULL_RATIO - 1.0), 0.0), 1.0)


def compute(
    p: Profile,
    f: ResidentFeatures,
    baselines: Baselines,
    clock: NightClock,
    *,
    p_trend: float = 0.0,  # Snowflake FORECAST (Phase 5+); 0 for now
) -> PriorityResult:
    tz: ZoneInfo = clock.tz
    rid = p.resident_id
    block = tod_block(f.as_of, tz)
    concern = note_concern(f.note, f.note_extract)

    # SpO2 for NEWS2: newest reading within 15 min. Core temp + SBP: latest spot check within 12 h.
    news = news2.compute(
        news2.News2Inputs(
            rr=f.rr_5m,
            spo2=f.spo2_recent[0] if f.spo2_recent else None,
            hr=f.hr_5m,
            sbp=f.spot_sbp,
            temp=f.spot_temp,
            new_confusion=None if concern is None else concern.new_or_worse_confusion,
            copd=p.copd,
        )
    )

    # P_anom: Snowflake ANOMALY_DETECTION when fresh results exist for this resident, else Tiger z-scores.
    sf_devs = snowflake_deviations(f.anomaly) if f.anomaly else None
    devs = sf_devs if sf_devs is not None else deviations(f, rid, baselines, block)
    behavior = baselines.behavior_for(rid)
    exits = min(f.exits_tonight / max(behavior.night_bed_exits_p95 or 1.0, 1.0), 1.0) if f.exits_tonight else 0.0
    restless = restlessness(f, baselines.get(rid, block, "motion"))
    still = 0.0
    if f.worn and f.still_minutes is not None:
        still_p95 = behavior.still_minutes_p95 or 60.0
        still = min(f.still_minutes / (2 * max(still_p95, 1.0)), 1.0)
    mins_check = None if f.last_check_ts is None else (f.as_of - f.last_check_ts).total_seconds() / 60

    c = Components(
        p_news=min(news.total / NEWS2_FULL, 1.0),
        p_anom=p_anom(devs),
        p_trend=p_trend,
        p_behav=max(exits, restless, still),
        p_concern=0.0 if concern is None else concern.p_concern,
        p_overdue=1.0 if mins_check is None else min(max(mins_check, 0.0) / OVERDUE_FULL_MIN, 1.0),
        anom_source="snowflake" if sf_devs is not None else "tiger_z",
        anom_as_of=f.anomaly[0]["as_of"].isoformat() if f.anomaly else None,
        news2_total=news.total,
        anomalous_metrics=[m for m, d in devs.items() if d.anomalous],
        bed_exits=exits,
        restlessness=restless,
        still=still,
        minutes_since_check=mins_check,
    )
    c.multi_signal = len(c.anomalous_metrics) >= MULTI_SIGNAL_MIN

    rules, device = evaluate_rules(f, p, baselines, clock)
    score = final_score(c, rules, device, news)
    factors = top_factors(p, f, c, news, devs, rules, device, concern, block, clock, behavior)
    return PriorityResult(rid, score, tier_for_score(score), c, factors, news, rules, device, concern, devs)


# --------------------------------------------------------------------------- factor text


def _fmt_minutes(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min"


def _deviation_text(d: MetricDeviation, p: Profile, block: int, short: bool = False) -> str:
    """'HR +22% vs her night normal'; short=True drops the label ('+22% vs her night normal')."""
    label = "" if short else METRIC_LABEL[d.metric] + " "
    if d.source == "snowflake" and d.lower is not None and d.upper is not None:
        return _expected_text(d, p, label, short)
    when = BLOCK_NAMES.get(block, "usual")
    whose = f"{p.pronoun} {when} normal"
    if d.metric == "skin_temp":
        return f"{label}{d.value - d.p50:+.1f} °C vs {whose}"
    if d.metric == "spo2":
        return f"usual {d.p50:.0f}%" if short else f"{label}{d.value:.0f}% (usual {d.p50:.0f}%)"
    if d.metric == "motion":
        ratio = d.value / max(d.p50, 0.5)
        if ratio >= 10:
            return f"Restless: awake and moving in bed vs {whose}"
        return f"Restless: movement {ratio:.0f}× {whose}" if ratio >= 1.5 else f"Movement unusual vs {whose}"
    pct = (d.value - d.p50) / d.p50 * 100 if d.p50 else 0.0
    return f"{label}{pct:+.0f}% vs {whose}"


def _expected_text(d: MetricDeviation, p: Profile, label: str, short: bool) -> str:
    """Snowflake result as a chip: the value against the model's expected range for this resident."""
    whose = f"{p.pronoun} expected"
    lo, hi = d.lower, d.upper
    if d.metric == "skin_temp":
        return f"{label}{d.value - d.p50:+.1f} °C vs {whose}"
    if d.metric == "motion":
        ratio = d.value / max(d.p50, 1.0)
        return f"Restless: movement {ratio:.0f}× {whose}" if ratio >= 1.5 else f"Movement unusual vs {whose}"
    unit = "%" if d.metric == "spo2" else ""
    rng = f"{whose} {lo:.0f}–{hi:.0f}{unit}"
    return rng if short else f"{label}{d.value:.0f}{unit} ({rng})"


_NEWS2_TEXT = {
    "rr": lambda v: f"RR {v:.0f}/min",
    "spo2": lambda v: f"SpO₂ {v:.0f}%",
    "hr": lambda v: f"HR {v:.0f}",
    "sbp": lambda v: f"BP {v:.0f} systolic",
    "temp": lambda v: f"Temp {v:.1f} °C",
}


def top_factors(
    p: Profile,
    f: ResidentFeatures,
    c: Components,
    news: news2.News2Result,
    devs: dict[str, MetricDeviation],
    rules: list[RuleHit],
    device: RuleHit | None,
    concern: NoteConcern | None,
    block: int,
    clock: NightClock,
    behavior,
    limit: int = 3,
) -> list[Factor]:
    """The `limit` largest contributions to the score, as plain-language strings."""
    cand: dict[str, Factor] = {}

    def add(key: str, text: str, points: float, kind: str) -> None:
        if points < MIN_FACTOR_POINTS:
            return
        prev = cand.get(key)
        if prev is None:
            cand[key] = Factor(key, text, points, kind)
        else:  # same signal from two components: one chip, combined weight
            keep = prev.text if prev.points >= points else text
            cand[key] = Factor(key, keep, prev.points + points, prev.kind)

    for hit in rules:
        add(f"rule:{hit.rule}", hit.text, float(hit.score), "rule")

    # Vital signs: NEWS2 points and the personal-baseline deviation of the same metric share one chip.
    news_pts = 100 * WEIGHTS["news"] * c.p_news
    anom_pts = 100 * WEIGHTS["anom"] * c.p_anom
    total_share = sum(d.share for d in devs.values())
    for m in ("hr", "rr", "spo2", "sbp", "temp", "skin_temp", "motion"):
        param = news.params.get(m)
        dev = devs.get(m)
        pts_news = news_pts * param.points / news.total if param is not None and param.points else 0.0
        pts_dev = (
            anom_pts * dev.share / total_share if dev is not None and total_share and dev.severity >= 0.25 else 0.0
        )
        if pts_news and pts_dev and m in _NEWS2_TEXT and not (m == "spo2" and round(dev.p50) == round(dev.value)):
            text = f"{_NEWS2_TEXT[m](float(param.value))}, {_deviation_text(dev, p, block, short=True)}"
        elif pts_news:
            text = _NEWS2_TEXT[m](float(param.value)) + f" (NEWS2 +{param.points})"
        elif pts_dev:
            text = _deviation_text(dev, p, block)
        else:
            continue
        add(m, text, pts_news + pts_dev, "vital")

    if concern is not None and f.note is not None:
        pts = 100 * WEIGHTS["concern"] * c.p_concern
        cons = news.params.get("consciousness")
        if cons is not None:
            pts += news_pts * cons.points / max(news.total, 1)
        when = clock.hhmm(f.note.ts)
        if concern.new_or_worse_confusion:
            add("concern", f"New confusion (note {when})", pts, "concern")
        elif concern.concern_level in ("med", "high"):
            add("concern", f"Staff concern (note {when})", pts, "concern")

    behav_pts = 100 * WEIGHTS["behav"] * c.p_behav
    parts = {"bed_exits": c.bed_exits, "restless": c.restlessness, "still": c.still}
    top_behav = max(parts, key=parts.get)
    if parts[top_behav] > 0:
        if top_behav == "bed_exits":
            usual = behavior.night_bed_exits_p95
            if usual is None:
                usual_txt = ""
            elif usual < 0.5:
                usual_txt = " (usually none)"
            else:
                usual_txt = f" (usual ≤ {usual:.0f})"
            text = f"{f.exits_tonight} bed exit{'s' if f.exits_tonight != 1 else ''} tonight{usual_txt}"
        elif top_behav == "restless":
            text = "Restless in bed"
        else:
            text = f"No movement for {_fmt_minutes(f.still_minutes or 0)}"
        add("motion" if top_behav == "restless" else top_behav, text, behav_pts, "behavior")

    if c.minutes_since_check is None:
        add("overdue", "No check logged in 24 h", 100 * WEIGHTS["overdue"], "overdue")
    elif c.minutes_since_check >= 60:
        add("overdue", f"Not checked for {_fmt_minutes(c.minutes_since_check)}", 100 * WEIGHTS["overdue"] * c.p_overdue,
            "overdue")  # fmt: skip

    if device is not None:
        add("device", device.text, float(device.score) / 3, "device")  # shown, but below any clinical signal

    return sorted(cand.values(), key=lambda x: x.points, reverse=True)[:limit]
