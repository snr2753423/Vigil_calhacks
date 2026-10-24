"""One plain-language sentence per resident for the Rounds Queue and the resident page (SPEC §7d, §11).

`plain_reason()` rewords the deterministic top factors (`priority.top_factors`) into a sentence an aide reads
at arm's length: "Breathing faster and heart rate higher than usual for her, and restless in bed." It never
adds a claim the factors don't carry and never calls an AI model; the exact factor strings stay on the card as
the small "facts" line. `plain_task()` turns a device issue into a chore ("Put it back on next time you pass
Room 106"), never an alarm.
"""

from __future__ import annotations

from datetime import datetime

from app.scoring.features import Profile, ResidentFeatures
from app.scoring.priority import PriorityResult
from app.scoring.rules import NightClock, RuleHit

# Personal-baseline comparisons ("… than usual for her"): metric → (when above usual, when below usual).
_COMPARED = {
    "rr": ("breathing faster", "breathing slower"),
    "hr": ("heart rate higher", "heart rate lower"),
    "spo2": ("oxygen higher", "oxygen lower"),
    "skin_temp": ("skin warmer", "skin cooler"),
    "motion": ("moving more", "moving less"),
}
# NEWS2-only vitals (no personal-baseline deviation behind the factor): the standard chart's direction.
_ABSOLUTE = {
    "rr": lambda v: "breathing fast" if v > 20 else "breathing slowly",
    "hr": lambda v: "heart rate high" if v > 90 else "heart rate low",
    "spo2": lambda v: "oxygen low",
    "sbp": lambda v: "blood pressure low" if v <= 110 else "blood pressure high",
    "temp": lambda v: "temperature raised" if v > 38.0 else "temperature low",
}
_VITALS = ("hr", "rr", "spo2", "sbp", "temp", "skin_temp")
_TREND = {
    ("rr", "up"): "breathing forecast to keep rising",
    ("rr", "down"): "breathing forecast to keep slowing",
    ("spo2", "down"): "oxygen forecast to keep falling",
    ("hr", "up"): "heart rate forecast to keep rising",
    ("hr", "down"): "heart rate forecast to keep falling",
}


def clock_12h(clock: NightClock, ts: datetime) -> str:
    """'2:14 AM' on the facility (display) clock."""
    t = clock.display(ts)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def duration(minutes: float) -> str:
    """'45 min', '1 h 10 min', '3 h'."""
    m = int(round(minutes))
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def _join(parts: list[str], word: str = "and") -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {word} " + parts[-1]


def _join_clauses(parts: list[str]) -> str:
    """Top-level clauses, with a comma before the last once a clause is long ('X and Y than usual for her, and Z')."""
    if len(parts) >= 3 or (len(parts) == 2 and any(" and " in x or " than usual " in x for x in parts)):
        return ", ".join(parts[:-1]) + ", and " + parts[-1]
    return _join(parts)


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + ("" if text.endswith((".", "?", "!")) else ".")


def _rule_sentence(hit: RuleHit, p: Profile, f: ResidentFeatures, clock: NightClock) -> str:
    she = "she" if p.sex == "F" else "he"
    if hit.rule == "fall":
        at = f" at {clock_12h(clock, hit.since)}" if hit.since else ""
        return f"May have fallen{at}: a hard knock on the wristband, then no movement."
    if hit.rule == "fall_prevention":
        at = f" since {clock_12h(clock, hit.since)}" if hit.since else ""
        needs = {"1-assist": "needs 1 person to help", "2-assist": "needs 2 people to help",
                 "bedbound": "is bedbound"}.get(p.mobility, "is at risk of falling")  # fmt: skip
        return f"Out of bed{at} without staff; {she} {needs}."
    if hit.rule == "unresponsive":
        lost = "heart-rate signal lost" in hit.text
        tail = "the heart-rate signal is lost" if lost else "heart rate lower than usual"
        return f"Possibly unresponsive: not moving for far longer than usual, and {tail}."
    if hit.rule == "hr_extreme":
        return "Heart rate very " + ("low" if "or lower" in hit.text else "high") + " for over a minute."
    if hit.rule == "rr_extreme":
        return "Breathing very " + ("slow" if "or lower" in hit.text else "fast") + " for over 2 minutes."
    if hit.rule == "spo2_low":
        return "Oxygen very low on two readings in a row."
    return _sentence(hit.text)


def plain_reason(
    p: Profile, f: ResidentFeatures, r: PriorityResult, clock: NightClock, rule: RuleHit | None = None
) -> str:
    """The top factors as one sentence with the resident's pronoun. Deterministic: same inputs, same words.
    `rule`: the rule in force for the card (the alert gate holds one for a while after it stops firing); it leads."""
    if rule is not None and rule.kind != "device":
        return _rule_sentence(rule, p, f, clock)
    keys = {x.key for x in r.factors}
    for hit in r.rules:  # a fired rule leads (score 100 / 90): say exactly that
        if f"rule:{hit.rule}" in keys:
            return _rule_sentence(hit, p, f, clock)

    him = "her" if p.sex == "F" else "him"
    compared_keys: set[str] = set()
    compared: list[str] = []
    rest: list[tuple[int, str]] = []  # (order, clause): vitals, then the forecast, notes, behaviour, the round
    for x in r.factors:
        k = x.key
        if k in _VITALS:
            dev = r.deviations.get(k)
            param = r.news2.params.get(k)
            if dev is not None and dev.severity >= 0.25 and k in _COMPARED:
                compared.append(_COMPARED[k][0 if dev.value >= dev.p50 else 1])
                compared_keys.add(k)
            elif param is not None and param.points:
                rest.append((1, _ABSOLUTE[k](float(param.value))))
        elif k == "trend":
            c = r.components
            if c.trend_metric in compared_keys:  # "breathing faster …, and the forecast says it will keep rising"
                verb = "rising" if c.trend_direction == "up" else "falling"
                rest.append((2, f"the forecast says it will keep {verb}"))
            else:
                rest.append((2, _TREND.get((c.trend_metric, c.trend_direction), "vitals forecast to keep changing")))
        elif k == "concern":
            if r.concern is not None and r.concern.new_or_worse_confusion:
                rest.append((3, "a staff note says " + ("she" if p.sex == "F" else "he") + " seems more confused"))
            else:
                rest.append((3, "a staff note raised a concern"))
        elif k == "motion":
            rest.append((4, "restless in bed"))
        elif k == "bed_exits":
            rest.append((4, _exits_clause(f.exits_tonight, r.exits_usual)))
        elif k == "still":
            rest.append((4, f"has not moved for {duration(f.still_minutes or 0)}, longer than usual"))
        elif k == "overdue":
            m = r.components.minutes_since_check
            rest.append((5, "no check logged today" if m is None else f"not checked for {duration(m)}"))
        # "device": a chore, said by plain_task(), not a reason to worry

    parts = ([f"{_join(compared)} than usual for {him}"] if compared else []) + [
        t for _, t in sorted(rest, key=lambda o: o[0])
    ]
    if not parts:
        return "Resting as usual." if r.device is None else "Resting as usual; see the device task."
    return _sentence(_join_clauses(parts))


_TIMES = {1: "once", 2: "twice"}


def _exits_clause(n: int, usual: float | None) -> str:
    """'up out of bed twice tonight, more than usual': 'more than usual' only when above her night p95 (rounded,
    as the factor chip shows it)."""
    times = _TIMES.get(n, f"{n} times")
    more = usual is not None and n > round(usual)
    return f"up out of bed {times} tonight" + (", more than usual" if more else "")


def plain_task(p: Profile, device: RuleHit | None, as_of: datetime) -> str | None:
    """A device issue as a chore in plain words, or None."""
    if device is None:
        return None
    first = p.name or f"Resident {p.resident_id}"
    room = f"Room {p.room}" if p.room else "their room"
    gone = f" for {duration((as_of - device.since).total_seconds() / 60)}" if device.since else ""
    if device.rule == "band_off":
        return f"{first}'s wristband has been off{gone}. Put it back on next time you pass {room}."
    return f"No signal from {first}'s sensors{gone}. Check the wristband and bed sensor next time you pass {room}."
