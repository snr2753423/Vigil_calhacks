"""Plain-language reason and device task for the cast (SPEC §8, §11): deterministic, never AI-written."""

from app.scoring import plain, priority
from app.scoring.features import Note, Window
from app.scoring.rules import RuleHit
from tests.scoring_helpers import NIGHT, ago, baselines, clock, features, normal_window, profile
from tests.test_trend import payload, points

C = clock()


def reason(p, f, b=None) -> tuple[str, priority.PriorityResult]:
    r = priority.compute(p, f, b or baselines(p.resident_id), C)
    return plain.plain_reason(p, f, r, C), r


def alvarez(**kw):
    """Rosa Alvarez at 02:14: HR, RR, skin temp up and restless in bed vs her own night; checked at 23:40."""
    w = normal_window() | {"hr": 84.0, "rr": 21.0, "skin_temp": 34.8, "motion": 7.0}
    note = Note(ago(NIGHT, hours=4, minutes=44), "A bit restless at bedtime, only drank half her fluids.")
    base = {"w0": w, "w1": w, "hr_5m": 84.0, "rr_5m": 21.0, "note": note, "last_check_ts": ago(NIGHT, minutes=154)}
    return features(rid=17, **(base | kw))


ROSA = profile(17, name="Rosa Alvarez", room="301", unit="Memory Care")


def test_alvarez_at_0214_reads_as_one_plain_sentence() -> None:
    text, r = reason(ROSA, alvarez())
    assert r.tier == "HIGH"
    assert text == "Breathing faster and heart rate higher than usual for her, and restless in bed."
    assert [f.text for f in r.factors][0] == "RR 21/min, +40% vs her night normal"  # the facts line is unchanged


def test_alvarez_after_the_confusion_note() -> None:
    w = normal_window() | {"hr": 84.0, "rr": 21.0, "skin_temp": 34.8, "motion": 2.0}
    note = Note(ago(NIGHT, minutes=1), "Seems more confused than usual, warm to touch.")
    text, _ = reason(ROSA, alvarez(w0=w, w1=w, note=note))
    assert text == (
        "Breathing faster and heart rate higher than usual for her, and a staff note says she seems more confused."
    )


def test_lee_uses_his_pronoun_and_the_forecast() -> None:
    fc = payload({"rr": {"points": points([22, 23, 24, 25.5], NIGHT), "last_value": 22.0}}, NIGHT)
    w = normal_window() | {"rr": 22.0}
    text, _ = reason(profile(42, sex="M", name="Henry Lee"), features(rid=42, w0=w, w1=w, rr_5m=22.0, forecast=fc))
    assert text == "Breathing faster than usual for him, and the forecast says it will keep rising."


def test_patel_unresponsive_rule_leads() -> None:
    f = features(rid=101, still_minutes=85, hr_5m=48)
    text, r = reason(profile(101, sex="M", name="Arthur Patel"), f, baselines(101, still_p95=40.0))
    assert r.score == 100
    assert text == "Possibly unresponsive: not moving for far longer than usual, and heart rate lower than usual."


def test_ortiz_fall_in_plain_words_with_a_12_hour_time() -> None:
    f = features(rid=110, in_bed=False, impact_ts=ago(NIGHT, seconds=40), impact_g=4.5, motion_after_impact=0.2,
                 rows_after_impact=6)  # fmt: skip
    text, _ = reason(profile(110, sex="M", name="James Ortiz", morse=75), f)
    assert text == "May have fallen at 2:13 AM: a hard knock on the wristband, then no movement."


def test_grant_bed_exit_without_staff() -> None:
    f = features(rid=120, in_bed=False, last_exit_ts=ago(NIGHT, minutes=1), last_check_ts=ago(NIGHT, minutes=40))
    text, r = reason(profile(120, sex="M", name="Thomas Grant", morse=70), f)
    assert r.score == 90
    assert text == "Out of bed since 2:13 AM without staff; he needs 1 person to help."


def test_kim_copd_and_rossi_afib_rest_as_usual() -> None:
    kim, _ = reason(profile(88, name="Margaret Kim", flags=("copd",)), features(rid=88, spo2_recent=(90.0, 90.0)))
    assert kim == "Resting as usual."
    rossi, _ = reason(profile(91, name="Elena Rossi", flags=("afib",)), features(rid=91))
    assert rossi == "Resting as usual."


def test_wright_band_off_is_a_task_not_a_worry() -> None:
    p = profile(33, name="Doris Wright", room="106")
    f = features(rid=33, worn=False, last_worn_ts=ago(NIGHT, minutes=70), hr_5m=None, hr_60s=Window(), spo2_recent=())
    text, r = reason(p, f)
    assert text == "Resting as usual; see the device task."
    assert plain.plain_task(p, r.device, f.as_of) == (
        "Doris Wright's wristband has been off for 1 h 10 min. Put it back on next time you pass Room 106."
    )
    assert plain.plain_task(p, None, f.as_of) is None


def test_behavior_and_overdue_wording() -> None:
    exits, _ = reason(profile(), features(exits_tonight=4), baselines(exits_p95=2.0))
    assert exits == "Up out of bed 4 times tonight, more than usual."
    overdue, _ = reason(profile(), features(last_check_ts=ago(NIGHT, minutes=150)))
    assert overdue == "Not checked for 2 h 30 min."


def test_deterministic() -> None:
    assert reason(ROSA, alvarez())[0] == reason(ROSA, alvarez())[0]


def test_duration_and_clock() -> None:
    assert [plain.duration(m) for m in (5, 60, 70, 156)] == ["5 min", "1 h", "1 h 10 min", "2 h 36 min"]
    assert plain.clock_12h(C, NIGHT) == "2:14 AM"


def test_bed_exits_say_more_than_usual_only_when_it_is() -> None:
    once, _ = reason(profile(), features(exits_tonight=1), baselines(exits_p95=1.0))
    assert once == "Up out of bed once tonight."
    twice, _ = reason(profile(), features(exits_tonight=2), baselines(exits_p95=0.0))
    assert twice == "Up out of bed twice tonight, more than usual."


def test_vitals_lead_then_behaviour() -> None:
    """Alvarez at 02:14 in the seeded replay: RR vs her expected, a bed exit, SpO₂ scoring NEWS2 points."""
    w = normal_window() | {"rr": 21.0}
    f = alvarez(w0=w, w1=w, hr_5m=64.0, spo2_recent=(92.0, 92.0), exits_tonight=1)
    text, r = reason(ROSA, f, baselines(17, exits_p95=1.0))
    assert [x.key for x in r.factors][:1] == ["rr"]
    assert text.startswith("Breathing faster than usual for her, oxygen low")


def test_a_rule_the_gate_still_holds_leads() -> None:
    """The alert gate keeps a rule in force after it stops firing: the sentence must follow the card, not go quiet."""
    held = RuleHit("rr_extreme", "critical", 100, "RR 28/min or higher for 2 min+")
    f = features()
    r = priority.compute(profile(), f, baselines(), C)
    assert r.rules == []
    assert plain.plain_reason(profile(), f, r, C, held) == "Breathing very fast for over 2 minutes."
    device = RuleHit("band_off", "device", 30, "Wristband off for 20 min")
    assert plain.plain_reason(profile(), f, r, C, device) == "Resting as usual."
