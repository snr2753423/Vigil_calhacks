"""P_trend from a Snowflake FORECAST (SPEC §7d) and its wiring into the priority score."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.scoring import priority, trend
from tests.scoring_helpers import NIGHT, baselines, clock, features, profile

TZ = ZoneInfo("America/Los_Angeles")
T0 = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)  # 01:00 facility time


def points(values: list[float], start: datetime = T0) -> list[list]:
    return [[(start + timedelta(minutes=15 * i)).isoformat(), v, v - 1, v + 1] for i, v in enumerate(values)]


def payload(metrics: dict, as_of: datetime = T0, copd: bool = False) -> dict:
    return {"as_of": as_of.isoformat(), "copd": copd, "metrics": metrics}


def test_crossing_is_the_end_of_the_first_bucket_in_the_zone() -> None:
    c = trend.crossing("rr", points([21, 22, 23, 24, 25.2, 26]), last_value=20.3)
    assert c == {"threshold": 25, "direction": "up", "bucket": points([0] * 5)[4][0],
                 "crosses_at": (T0 + timedelta(minutes=75)).isoformat()}  # fmt: skip


def test_no_crossing_when_already_in_the_zone_or_within_noise() -> None:
    assert trend.crossing("rr", points([26, 27]), last_value=25.5) is None  # NEWS2 already scores it
    # Lee's SpO₂ minimum sat at 92 %: a forecast drifting to 91 % is noise, not a trend
    assert trend.crossing("spo2", points([91.6, 91.0]), last_value=92.0) is None
    assert trend.crossing("spo2", points([92, 90.5]), last_value=94.0)["threshold"] == 91


def test_copd_uses_scale_2() -> None:
    assert trend.crossing("spo2", points([89, 88]), last_value=90.0, copd=True) is None
    assert trend.crossing("spo2", points([86, 82.5]), last_value=88.0, copd=True)["threshold"] == 83


def test_hr_crosses_either_way() -> None:
    assert trend.crossing("hr", points([45, 39]), last_value=52.0)["direction"] == "down"
    assert trend.crossing("hr", points([120, 132]), last_value=110.0)["direction"] == "up"


@pytest.mark.parametrize(("minutes", "expected"), [(60, 1.0), (120, 1.0), (180, 0.5), (240, 0.5), (255, 0.0)])
def test_p_trend_by_lead_time(minutes: int, expected: float) -> None:
    n = minutes // 15  # the crossing bucket ends `minutes` after T0
    values = [20.0] * (n - 1) + [26.0]
    p, metric, _ = trend.p_trend(payload({"rr": {"points": points(values), "last_value": 20.0}}), T0)
    assert p == expected
    assert (metric == "rr") == (expected > 0)


def test_p_trend_still_counts_a_crossing_predicted_before_as_of() -> None:
    p, _, _ = trend.p_trend(payload({"rr": {"points": points([24, 26]), "last_value": 20.0}}), T0 + timedelta(hours=1))
    assert p == 1.0


def test_annotate_writes_the_callout_in_facility_time() -> None:
    out = trend.annotate(payload({"rr": {"points": points([21, 22, 23, 24, 25.2]), "last_value": 20.3}}), TZ)
    assert out["callout"] == "RR forecast to cross 25 by ~02:15"
    assert out["p_trend"] == 1.0 and out["metrics"]["rr"]["crossing"]["threshold"] == 25
    low = trend.annotate(payload({"spo2": {"points": points([93, 90]), "last_value": 95.0}}), TZ)
    assert low["callout"] == "SpO₂ forecast to fall to 91% by ~01:30"


def test_priority_reads_the_forecast_for_p_trend_and_a_factor_chip() -> None:
    fc = payload({"rr": {"points": points([16, 17, 18, 19], NIGHT), "last_value": 15.0}}, NIGHT)
    fc["metrics"]["rr"]["points"][-1][1] = 25.5  # crosses within the hour
    base = priority.compute(profile(), features(), baselines(), clock())
    with_fc = priority.compute(profile(), features(forecast=fc), baselines(), clock())
    assert base.components.p_trend == 0
    assert with_fc.components.p_trend == 1.0 and with_fc.components.trend_metric == "rr"
    assert with_fc.score - base.score == pytest.approx(15, abs=0.1)
    chip = next(f for f in with_fc.factors if f.kind == "trend")
    assert chip.text.startswith("RR forecast to cross 25 by ~")
