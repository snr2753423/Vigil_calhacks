"""Priority score (SPEC §7d): exact formula, multi-signal boost, floors, tiers, deterministic factors."""

import pytest

from app.scoring import priority
from app.scoring.baseline import severity, zscore
from app.scoring.features import Note, Window
from app.scoring.priority import Components, MetricDeviation
from app.scoring.rules import RuleHit
from tests.scoring_helpers import DAY, NIGHT, ago, baselines, clock, features, normal_window, profile


def test_formula_weights() -> None:
    assert Components(p_news=1).base_score == pytest.approx(30)
    assert Components(p_anom=1).base_score == pytest.approx(25)
    assert Components(p_trend=1).base_score == pytest.approx(15)
    assert Components(p_behav=1).base_score == pytest.approx(10)
    assert Components(p_concern=1).base_score == pytest.approx(12)
    assert Components(p_overdue=1).base_score == pytest.approx(8)
    c = Components(p_news=0.5, p_anom=0.4, p_trend=0.0, p_behav=0.3, p_concern=0.6, p_overdue=0.25)
    assert c.base_score == pytest.approx(100 * (0.15 + 0.10 + 0.03 + 0.072 + 0.02))


def test_multi_signal_boost_and_cap() -> None:
    assert priority.combine_formula(Components(p_news=0.5, multi_signal=True)) == pytest.approx(25)
    full = Components(p_news=1, p_anom=1, p_trend=1, p_behav=1, p_concern=1, p_overdue=1, multi_signal=True)
    assert priority.combine_formula(full) == 100


def test_p_anom_combines_metrics() -> None:
    devs = {m: MetricDeviation(m, 0, 2, 0.5, 0, 0) for m in ("hr", "rr")}
    assert priority.p_anom(devs) == pytest.approx(0.75)
    assert priority.p_anom({}) == 0


def test_tiger_fallback_severity() -> None:
    b = baselines().get(7, 0, "hr")  # p05 60, p50 64, p95 70 → σ = 10 / 3.29
    assert zscore(70, b, "hr") == pytest.approx(6 / (10 / 3.29))
    assert severity(2.0, "hr") == pytest.approx(0.5)
    assert severity(-6.0, "hr") == 1.0
    assert severity(3.0, "spo2") == 0  # high SpO2 is not a concern
    assert severity(-3.0, "motion") == 0  # stiller than usual is not restlessness


def test_tiers() -> None:
    assert priority.tier_for_score(80) == "CRITICAL"
    assert priority.tier_for_score(79.9) == "HIGH"
    assert priority.tier_for_score(60) == "HIGH"
    assert priority.tier_for_score(59.9) == "WATCH"
    assert priority.tier_for_score(35) == "WATCH"
    assert priority.tier_for_score(34.9) == "STABLE"


def test_resident_at_their_normal_is_stable() -> None:
    r = priority.compute(profile(), features(), baselines(), clock())
    c = r.components
    assert c.p_news == 0 and c.p_anom == 0 and c.p_concern == 0
    assert c.p_behav == pytest.approx(10 / 120)  # 10 still minutes vs 2 × p95 60
    assert c.p_overdue == pytest.approx(20 / 120)
    assert r.score == pytest.approx(round(100 * (0.10 * 10 / 120 + 0.08 * 20 / 120), 1))
    assert r.tier == "STABLE" and r.rules == [] and r.device is None


def alvarez_like(**kw):
    w = normal_window() | {"hr": 84.0, "rr": 21.0, "skin_temp": 34.8, "motion": 2.0}
    note = Note(ago(NIGHT, hours=4, minutes=44), "Rosa is more confused than usual, only drank half her fluids.")
    return features(w0=w, w1=w, hr_5m=84.0, rr_5m=21.0, exits_tonight=4, note=note, **kw)


def test_silent_drift_scores_exactly_per_formula() -> None:
    r = priority.compute(profile(), alvarez_like(), baselines(exits_p95=2.0), clock())
    c = r.components
    assert r.news2.total == 2 + 3  # RR 21 → 2, new confusion → 3 (HR 84 scores 0)
    assert c.p_news == pytest.approx(5 / 7)
    assert c.p_anom == 1.0  # HR z ≈ 6.6 → severity 1
    assert c.p_behav == 1.0  # 4 exits vs usual ≤ 2
    assert c.p_concern == 1.0  # new/worse confusion
    assert c.multi_signal  # HR, RR, skin temp
    expected = 100 * (0.30 * 5 / 7 + 0.25 + 0.10 + 0.12 + 0.08 * 20 / 120) + 10
    assert r.score == pytest.approx(round(expected, 1))
    assert r.tier == "HIGH"


def test_silent_drift_factors_are_plain_language_and_deterministic() -> None:
    r = priority.compute(profile(), alvarez_like(), baselines(exits_p95=2.0), clock())
    texts = [f.text for f in r.factors]
    assert texts == ["New confusion (note 21:30)", "RR 21/min, +40% vs her night normal", "HR +31% vs her night normal"]
    again = priority.compute(profile(), alvarez_like(), baselines(exits_p95=2.0), clock())
    assert [f.to_dict() for f in again.factors] == [f.to_dict() for f in r.factors]
    assert [f.points for f in r.factors] == sorted((f.points for f in r.factors), reverse=True)


def test_factor_wording_follows_sex_and_time_of_day() -> None:
    w = normal_window() | {"hr": 84.0}
    r = priority.compute(profile(sex="M"), features(DAY, w0=w, w1=w, hr_5m=84.0), baselines(), clock())
    assert "HR +31% vs his afternoon normal" in [f.text for f in r.factors]


def test_news2_points_and_baseline_share_one_chip() -> None:
    w = normal_window() | {"rr": 25.0}
    r = priority.compute(profile(sex="M"), features(w0=w, w1=w, rr_5m=25.0), baselines(), clock())
    rr = [f for f in r.factors if f.key == "rr"]
    assert len(rr) == 1 and rr[0].text == "RR 25/min, +67% vs his night normal"


def test_news2_three_forces_high() -> None:
    r = priority.compute(profile(), features(spo2_recent=(90.0,)), baselines(), clock())
    assert r.news2.any_three and r.score >= 60 and r.tier == "HIGH"


def test_rule_floors() -> None:
    fall = features(impact_ts=ago(NIGHT, seconds=40), impact_g=4.5, motion_after_impact=0.2, rows_after_impact=6,
                    in_bed=False)  # fmt: skip
    r = priority.compute(profile(), fall, baselines(), clock())
    assert r.score == 100 and r.tier == "CRITICAL" and r.rule.rule == "fall"
    assert r.factors[0].text.startswith("Suspected fall")

    exit_ = features(in_bed=False, last_exit_ts=ago(NIGHT, minutes=1), last_check_ts=ago(NIGHT, minutes=40))
    r = priority.compute(profile(), exit_, baselines(), clock())
    assert r.score == 90 and r.tier == "CRITICAL" and r.rule.rule == "fall_prevention"


def test_device_issue_alone_scores_30_and_is_not_an_alarm() -> None:
    f = features(worn=False, last_worn_ts=ago(NIGHT, minutes=30), hr_5m=None, hr_60s=Window(), spo2_recent=())
    r = priority.compute(profile(), f, baselines(), clock())
    assert r.score == 30 and r.tier == "STABLE"
    assert r.device and r.device.rule == "band_off" and r.rules == []


def test_motion_while_out_of_bed_is_not_restlessness() -> None:
    walking = normal_window() | {"motion": 300.0, "in_bed": 0.0}
    r = priority.compute(profile(), features(w0=walking, w1=walking, in_bed_frac_15m=0.0), baselines(), clock())
    assert "motion" not in r.deviations and r.components.restlessness == 0


def test_restless_in_bed() -> None:
    restless = normal_window() | {"motion": 6.0}  # 3× the usual median of 2
    r = priority.compute(profile(), features(w0=restless, w1=restless), baselines(), clock())
    assert r.components.restlessness == pytest.approx((3 - 1) / 4)
    assert "motion" in r.deviations


def test_overdue_and_never_checked() -> None:
    r = priority.compute(profile(), features(last_check_ts=ago(NIGHT, minutes=150)), baselines(), clock())
    assert r.components.p_overdue == 1.0
    assert "Not checked for 2 h 30 min" in [f.text for f in r.factors]
    r = priority.compute(profile(), features(last_check_ts=None), baselines(), clock())
    assert r.components.minutes_since_check is None and r.components.p_overdue == 1.0


def test_p_trend_is_wired_but_zero_until_snowflake_forecasts() -> None:
    base = priority.compute(profile(), features(), baselines(), clock())
    trend = priority.compute(profile(), features(), baselines(), clock(), p_trend=1.0)
    assert base.components.p_trend == 0
    assert trend.score - base.score == pytest.approx(15, abs=0.1)


def test_rule_hit_is_listed_first() -> None:
    hits = [RuleHit("hr_extreme", "critical", 100, "HR 140 or higher for 60 s+")]
    factors = priority.top_factors(
        profile(), features(), Components(), priority.news2.compute(priority.news2.News2Inputs()), {}, hits, None,
        None, 0, clock(), baselines().behavior_for(7),
    )  # fmt: skip
    assert factors[0].text == "HR 140 or higher for 60 s+"


def test_bed_exits_factor_text() -> None:
    r = priority.compute(profile(), features(exits_tonight=4), baselines(exits_p95=2.0), clock())
    assert r.factors[0].text == "4 bed exits tonight (usual ≤ 2)"
    r = priority.compute(profile(), features(exits_tonight=1), baselines(exits_p95=0.0), clock())
    assert r.factors[0].text == "1 bed exit tonight (usually none)"
