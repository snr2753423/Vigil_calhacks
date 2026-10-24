"""Fast-tier rules (SPEC §7a): the 5 critical rules, fall prevention, device issue, night window."""

from datetime import UTC, datetime, timedelta

from app.scoring import rules
from app.scoring.features import Window
from tests.scoring_helpers import DAY, NIGHT, TZ, ago, baselines, clock, features, profile

# --------------------------------------------------------------------------- 1. extreme HR


def test_hr_high_sustained_60s_fires() -> None:
    hit = rules.hr_extreme(features(hr_60s=Window(13, 131, 140, 60.0)))
    assert hit and hit.rule == "hr_extreme" and hit.score == 100 and "131" in hit.text


def test_hr_low_sustained_60s_fires() -> None:
    assert rules.hr_extreme(features(hr_60s=Window(13, 33, 39, 60.0)))


def test_hr_not_sustained_long_enough() -> None:
    assert rules.hr_extreme(features(hr_60s=Window(10, 131, 140, 45.0))) is None


def test_hr_one_reading_back_in_range_breaks_it() -> None:
    assert rules.hr_extreme(features(hr_60s=Window(13, 128, 140, 60.0))) is None
    assert rules.hr_extreme(features(hr_60s=Window(13, 130, 140, 60.0))) is None  # > 130, not ≥
    assert rules.hr_extreme(features(hr_60s=Window(13, 35, 40, 60.0))) is None  # < 40, not ≤


def test_hr_rule_needs_worn_readings() -> None:
    assert rules.hr_extreme(features(hr_60s=Window(0, None, None, 0.0))) is None


# --------------------------------------------------------------------------- 2. low SpO2


def test_spo2_two_consecutive_low_readings() -> None:
    p = profile()
    assert rules.spo2_low(features(spo2_recent=(88.0, 87.0)), p)
    assert rules.spo2_low(features(spo2_recent=(88.0, 92.0)), p) is None  # only one
    assert rules.spo2_low(features(spo2_recent=(87.0,)), p) is None  # only one reading
    assert rules.spo2_low(features(spo2_recent=(89.0, 89.0)), p) is None


def test_spo2_copd_threshold_is_84() -> None:
    p = profile(flags=("copd",))
    assert rules.spo2_low(features(spo2_recent=(86.0, 85.0)), p) is None
    assert rules.spo2_low(features(spo2_recent=(84.0, 83.0)), p)


# --------------------------------------------------------------------------- 3. extreme RR


def test_rr_high_sustained_2min_in_bed() -> None:
    assert rules.rr_extreme(features(rr_120s=Window(25, 28, 31, 120.0)))
    assert rules.rr_extreme(features(rr_120s=Window(25, 27.9, 31, 120.0))) is None
    assert rules.rr_extreme(features(rr_120s=Window(18, 29, 31, 85.0))) is None  # < 2 min


def test_rr_low_sustained_2min_in_bed() -> None:
    assert rules.rr_extreme(features(rr_120s=Window(25, 6, 8, 120.0)))
    assert rules.rr_extreme(features(rr_120s=Window(25, 6, 8.5, 120.0))) is None


# --------------------------------------------------------------------------- 4. possibly unresponsive


def test_unresponsive_still_and_hr_low() -> None:
    b = baselines(still_p95=40.0)  # 2 × 40 = 80 min; HR p05 = 60 → < 50
    hit = rules.unresponsive(features(still_minutes=85, hr_5m=48), profile(), b, TZ)
    assert hit and hit.rule == "unresponsive" and "85 min" in hit.text


def test_unresponsive_still_and_hr_signal_lost() -> None:
    b = baselines(still_p95=40.0)
    f = features(still_minutes=85, hr_5m=None, last_hr_ts=ago(NIGHT, minutes=3))
    assert rules.unresponsive(f, profile(), b, TZ)


def test_unresponsive_ignores_data_gap() -> None:
    """No rows at all (backend restart, network) is a device/data gap, not 'HR lost while worn'."""
    b = baselines(still_p95=40.0)
    gap = ago(NIGHT, minutes=6)
    f = features(still_minutes=85, hr_5m=None, last_hr_ts=gap, last_ts=gap, last_worn_ts=gap)
    assert rules.unresponsive(f, profile(), b, TZ) is None


def test_unresponsive_needs_all_conditions() -> None:
    b = baselines(still_p95=40.0)
    p = profile()
    assert rules.unresponsive(features(still_minutes=79, hr_5m=48), p, b, TZ) is None  # not still long enough
    assert rules.unresponsive(features(still_minutes=85, hr_5m=52), p, b, TZ) is None  # HR not low enough
    assert rules.unresponsive(features(still_minutes=85, hr_5m=48, worn=False), p, b, TZ) is None  # band off


# --------------------------------------------------------------------------- 5. suspected fall


def fall_features(**kw):
    base = {
        "impact_ts": ago(NIGHT, seconds=40),
        "impact_g": 4.5,
        "motion_after_impact": 0.3,
        "rows_after_impact": 6,
        "in_bed": False,
        "last_check_ts": ago(NIGHT, minutes=30),
    }
    return features(**(base | kw))


def test_fall_impact_then_still_out_of_bed() -> None:
    hit = rules.suspected_fall(fall_features(), clock())
    assert hit and hit.rule == "fall" and hit.score == 100 and "4.5 g" in hit.text


def test_fall_needs_30s_of_stillness() -> None:
    assert rules.suspected_fall(fall_features(impact_ts=ago(NIGHT, seconds=20)), clock()) is None
    assert rules.suspected_fall(fall_features(motion_after_impact=12.0), clock()) is None


def test_fall_small_bump_or_in_bed_is_not_a_fall() -> None:
    assert rules.suspected_fall(fall_features(impact_g=2.0), clock()) is None
    assert rules.suspected_fall(fall_features(in_bed=True), clock()) is None
    assert rules.suspected_fall(fall_features(back_in_bed_since_impact=True), clock()) is None


def test_fall_clears_when_a_check_is_logged_after_it() -> None:
    assert rules.suspected_fall(fall_features(last_check_ts=ago(NIGHT, seconds=5)), clock()) is None


# --------------------------------------------------------------------------- fall prevention


def exit_features(as_of=NIGHT, **kw):
    base = {"in_bed": False, "last_exit_ts": ago(as_of, minutes=1), "last_check_ts": ago(as_of, minutes=40)}
    return features(as_of, **(base | kw))


def test_fall_prevention_unassisted_night_exit() -> None:
    hit = rules.fall_prevention(exit_features(), profile(mobility="1-assist", morse=30), clock())
    assert hit and hit.score == 90 and hit.kind == "fall_prevention"


def test_fall_prevention_morse_alone_qualifies() -> None:
    assert rules.fall_prevention(exit_features(), profile(mobility="independent", morse=45), clock())
    assert rules.fall_prevention(exit_features(), profile(mobility="independent", morse=44), clock()) is None


def test_fall_prevention_not_during_the_day_unless_forced() -> None:
    f = exit_features(DAY)
    assert rules.fall_prevention(f, profile(), clock()) is None
    forced = clock(force_night=True, force_night_since=ago(DAY, minutes=10))
    assert rules.fall_prevention(f, profile(), forced)
    # an exit from before force-night was switched on doesn't count
    assert rules.fall_prevention(f, profile(), clock(force_night=True, force_night_since=ago(DAY, seconds=10))) is None


def test_fall_prevention_display_offset_moves_the_window() -> None:
    f = exit_features(DAY)  # 14:00 real → 02:00 displayed with a +12 h offset
    assert rules.fall_prevention(f, profile(), clock(offset_s=12 * 3600))


def test_fall_prevention_answered_call_light_or_check_suppresses() -> None:
    p = profile()
    assert rules.fall_prevention(exit_features(last_check_ts=ago(NIGHT, minutes=4)), p, clock()) is None  # before exit
    assert rules.fall_prevention(exit_features(last_check_ts=ago(NIGHT, seconds=10)), p, clock()) is None  # since
    assert rules.fall_prevention(exit_features(last_check_ts=ago(NIGHT, minutes=7)), p, clock())


def test_fall_prevention_clears_on_return_to_bed() -> None:
    f = exit_features(in_bed=True)
    assert rules.fall_prevention(f, profile(), clock()) is None
    f = exit_features(last_return_ts=ago(NIGHT, seconds=10), in_bed=None)
    assert rules.fall_prevention(f, profile(), clock()) is None


# --------------------------------------------------------------------------- device issue


def test_device_offline_after_5_min_gap() -> None:
    hit = rules.device_issue(features(last_ts=ago(NIGHT, minutes=6), heartbeat_live=False))
    assert hit and hit.rule == "device_offline" and hit.score == 30
    assert rules.device_issue(features(last_ts=None, heartbeat_live=None)).rule == "device_offline"
    assert rules.device_issue(features(last_ts=ago(NIGHT, minutes=4))) is None


def test_band_off_after_10_min() -> None:
    hit = rules.device_issue(features(worn=False, last_worn_ts=ago(NIGHT, minutes=11)))
    assert hit and hit.rule == "band_off"
    assert rules.device_issue(features(worn=False, last_worn_ts=ago(NIGHT, minutes=9))) is None


# --------------------------------------------------------------------------- night clock


def test_night_window_edges() -> None:
    c = clock()
    local = lambda h, m=0: datetime(2026, 10, 15, h, m, tzinfo=TZ).astimezone(UTC)  # noqa: E731
    assert not c.is_night(local(21, 59))
    assert c.is_night(local(22, 0))
    assert c.is_night(local(5, 59))
    assert not c.is_night(local(6, 0))


def test_night_start() -> None:
    c = clock()
    assert c.night_start(NIGHT) == datetime(2026, 10, 15, 22, 0, tzinfo=TZ)
    assert c.night_start(DAY) is None
    since = ago(DAY, minutes=30)
    assert clock(force_night=True, force_night_since=since).night_start(DAY) == since


def test_evaluate_rules_orders_critical_first() -> None:
    f = fall_features(last_exit_ts=ago(NIGHT, minutes=2))
    hits, device = rules.evaluate_rules(f, profile(), baselines(), clock())
    assert [h.rule for h in hits] == ["fall", "fall_prevention"]
    assert device is None
    assert timedelta(0) < NIGHT - hits[0].since < timedelta(minutes=1)
