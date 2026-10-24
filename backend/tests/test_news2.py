"""NEWS2 subset (SPEC §7b): every band edge, COPD Scale 2, totals and the HIGH trigger."""

import pytest

from app.scoring import news2
from app.scoring.news2 import News2Inputs


@pytest.mark.parametrize(
    ("rr", "points"),
    [
        (5, 3),
        (8, 3),
        (8.4, 3),
        (8.5, 1),
        (9, 1),
        (11, 1),
        (12, 0),
        (20, 0),
        (20.4, 0),
        (21, 2),
        (24, 2),
        (25, 3),
        (40, 3),
    ],
)
def test_rr_bands(rr, points) -> None:
    assert news2.score_rr(rr) == points


@pytest.mark.parametrize(("spo2", "points"), [(85, 3), (91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0)])
def test_spo2_scale1_bands(spo2, points) -> None:
    assert news2.score_spo2_scale1(spo2) == points


@pytest.mark.parametrize(
    ("spo2", "points"),
    [(80, 3), (83, 3), (84, 2), (85, 2), (86, 1), (87, 1), (88, 0), (90, 0), (92, 0), (93, 0), (97, 0), (100, 0)],
)
def test_spo2_scale2_room_air(spo2, points) -> None:
    assert news2.score_spo2_scale2(spo2) == points


@pytest.mark.parametrize(("spo2", "points"), [(92, 0), (93, 1), (94, 1), (95, 2), (96, 2), (97, 3), (100, 3)])
def test_spo2_scale2_on_oxygen(spo2, points) -> None:
    assert news2.score_spo2_scale2(spo2, on_oxygen=True) == points


@pytest.mark.parametrize(
    ("hr", "points"),
    [(30, 3), (40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3), (180, 3)],
)
def test_hr_bands(hr, points) -> None:
    assert news2.score_hr(hr) == points


@pytest.mark.parametrize(
    ("sbp", "points"),
    [(70, 3), (90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3), (250, 3)],
)
def test_sbp_bands(sbp, points) -> None:
    assert news2.score_sbp(sbp) == points


@pytest.mark.parametrize(
    ("temp", "points"),
    [
        (34.0, 3),
        (35.0, 3),
        (35.04, 3),
        (35.1, 1),
        (36.0, 1),
        (36.1, 0),
        (38.0, 0),
        (38.1, 1),
        (39.0, 1),
        (39.1, 2),
        (41, 2),
    ],
)
def test_temp_bands(temp, points) -> None:
    assert news2.score_temp(temp) == points


def test_consciousness() -> None:
    assert news2.score_consciousness(True) == 3
    assert news2.score_consciousness(False) == 0


def test_rounding_is_half_up_like_a_charted_observation() -> None:
    assert news2.score_rr(20.5) == 2  # 21, not banker's-rounded 20
    assert news2.score_hr(90.5) == 1
    assert news2.score_temp(38.05) == 1  # 38.1


def test_total_uses_only_available_inputs() -> None:
    r = news2.compute(News2Inputs(rr=22, spo2=95, hr=95))
    assert r.total == 2 + 1 + 1
    assert set(r.missing) == {"sbp", "temp", "consciousness"}
    assert r.spo2_scale == 1
    assert not r.forces_high


def test_copd_uses_scale_2() -> None:
    assert news2.compute(News2Inputs(spo2=89, copd=True)).total == 0
    assert news2.compute(News2Inputs(spo2=89, copd=False)).total == 3


def test_forces_high_on_any_three_or_total_five() -> None:
    assert news2.compute(News2Inputs(rr=25)).forces_high  # single 3
    assert news2.compute(News2Inputs(new_confusion=True)).forces_high
    r = news2.compute(News2Inputs(rr=22, hr=112, spo2=95))  # 2 + 2 + 1
    assert r.total == 5 and not r.any_three and r.forces_high
    assert not news2.compute(News2Inputs(rr=22, hr=112)).forces_high  # 4


def test_full_set() -> None:
    r = news2.compute(News2Inputs(rr=21, spo2=94, hr=88, sbp=108, temp=37.7, new_confusion=True))
    assert {k: p.points for k, p in r.params.items()} == {
        "rr": 2, "spo2": 1, "hr": 0, "sbp": 1, "temp": 0, "consciousness": 3,
    }  # fmt: skip
    assert r.total == 7
    assert r.to_dict()["forces_high"] is True
