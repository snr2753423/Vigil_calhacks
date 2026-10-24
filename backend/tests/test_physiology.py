"""Normal physiology (SPEC §8): parameter ranges, signal ranges, cadences, determinism."""

from datetime import UTC, datetime

import numpy as np
import pytest

from app.sim import cast
from app.sim import facility as fac
from app.sim.physiology import SPO2_PERIOD_S, ResidentSim, ar1, make_params

SEED = 1337
TZ = fac.tzinfo("America/Los_Angeles")
NOW = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)  # 14:00 facility time
DAYS = 4


@pytest.fixture(scope="module")
def roster() -> list[cast.Resident]:
    return cast.build_roster(SEED)


@pytest.fixture(scope="module")
def params(roster):
    return {r.resident_id: cast.resident_params(SEED, r) for r in roster}


def simulate(p, days: int = DAYS):
    """Normal physiology only (no scripted night)."""
    t0 = int(NOW.timestamp()) // 5 * 5 - days * 86400
    sim = ResidentSim(p, SEED, TZ)
    return sim.step(t0, days * 17280)


def local_hours(ts_us: np.ndarray) -> np.ndarray:
    t = (ts_us // 1_000_000).astype(np.int64)
    return fac.local_hours(t, fac.utc_offsets(t, TZ))


def test_ar1_matches_the_recursive_definition() -> None:
    rng = np.random.default_rng(0)
    eps = rng.normal(size=500)
    for phi in (0.5, 0.9, 0.995):
        expected, x = [], 0.3
        for e in eps:
            x = phi * x + e
            expected.append(x)
        np.testing.assert_allclose(ar1(eps, phi, 0.3), expected, rtol=1e-9, atol=1e-9)


def test_parameter_ranges_follow_spec(params) -> None:
    for p in params.values():
        if p.resident_id in cast.CAST_IDS:
            continue  # cast residents have pinned values, checked in test_cast
        if p.afib:
            assert 96 <= p.hr_base <= 102
        elif p.beta_blocker:
            assert 52 <= p.hr_base <= 64
        else:
            assert 58 <= p.hr_base <= 88
        assert 0.08 <= p.hr_sleep_drop <= 0.12 or p.afib
        assert p.hr_activity_gain <= 25
        assert 14 <= p.rr_base <= 19
        assert (88 <= p.spo2_base <= 92) if p.copd else (94 <= p.spo2_base <= 98)
        assert 33.0 <= p.skin_base <= 34.5
        assert (70 <= p.hrv_base <= 110) if p.afib else (15 <= p.hrv_base <= 40)
        assert 30 <= p.turn_cadence_min <= 90
        assert p.trips_per_night == 0 if p.bedbound else 0 <= p.trips_per_night <= 3
        assert p.phase_s % 5 == 0 and 0 <= p.phase_s < SPO2_PERIOD_S


def test_params_are_deterministic_and_seed_dependent(roster) -> None:
    r = roster[4]
    assert cast.resident_params(SEED, r) == cast.resident_params(SEED, r)
    assert cast.resident_params(SEED, r) != cast.resident_params(SEED + 1, r)


@pytest.mark.parametrize("rid", [1, 5, 50, 77, 95, 110])
def test_simulated_signals_stay_in_physiological_ranges(params, rid) -> None:
    p = params[rid]
    c = simulate(p).cols
    hr, rr, spo2, skin, hrv = c["hr"], c["rr"], c["spo2"], c["skin_temp"], c["hrv_rmssd"]
    assert np.nanmin(hr) >= p.hr_min and np.nanmax(hr) <= p.hr_max
    assert np.nanmin(rr) >= 11 and np.nanmax(rr) <= 22
    assert np.nanmin(spo2) >= p.spo2_lo and np.nanmax(spo2) <= 100
    assert np.nanmin(skin) >= 32.5 and np.nanmax(skin) <= 35.5
    assert np.nanmin(hrv) >= 5
    # Device masks: no wrist vitals while the band is off, no RR while out of bed.
    off = c["worn"] == 0
    assert np.isnan(hr[off]).all() and np.isnan(spo2[off]).all()
    assert np.isnan(rr[c["in_bed"] == 0]).all()
    assert (c["bed_motion"][c["in_bed"] == 0] == 0).all()


def test_night_heart_rate_dips_8_to_12_percent(params) -> None:
    p = next(p for p in params.values() if not (p.afib or p.beta_blocker or p.bedbound) and p.resident_id > 8)
    c = simulate(p, days=6).cols
    h = local_hours(c["ts"])
    asleep = (h >= 1) & (h < 4) & (c["in_bed"] == 1) & (c["motion"] < 3)
    resting_day = (h >= 14) & (h < 16) & (c["motion"] < 20) & (c["in_bed"] == 0)
    night, day = np.nanmedian(c["hr"][asleep]), np.nanmedian(c["hr"][resting_day])
    assert 0.80 <= night / day <= 0.95


def test_sampling_cadence(params) -> None:
    p = params[12]
    c = simulate(p, days=1).cols
    t = c["ts"] // 1_000_000
    spo2_t = t[~np.isnan(c["spo2"])]
    skin_t = t[~np.isnan(c["skin_temp"])]
    assert (np.diff(spo2_t) % 300 == 0).all()  # every 5 min; band-off only drops readings
    assert np.median(np.diff(skin_t)) == 60
    assert np.median(np.diff(t)) == 5


def test_daily_band_charging_and_bed_schedule(params) -> None:
    p = next(p for p in params.values() if p.mobility == "independent" and p.resident_id not in cast.CAST_IDS)
    c = simulate(p, days=DAYS).cols
    off_minutes = (c["worn"] == 0).sum() * 5 / 60
    assert 25 * DAYS <= off_minutes <= 40 * DAYS + 5  # ~30 min a day
    h = local_hours(c["ts"])
    assert c["in_bed"][(h >= 1) & (h < 4)].mean() > 0.9  # asleep in bed at night (bathroom trips aside)
    assert c["in_bed"][(h >= 10) & (h < 12)].mean() < 0.1  # up during the late morning


def test_staff_events_rounds_meals_and_spot_checks(params) -> None:
    p = params[60]
    batch = simulate(p, days=3)
    by_type: dict[str, list] = {}
    for e in batch.events:
        by_type.setdefault(e.type, []).append(e)
    rounds = [e for e in by_type["check_logged"] if e.payload["kind"] == "round"]
    assert 3 * 12 * 0.9 <= len(rounds) <= 3 * 12 + 1  # every 2 h, a few skipped
    for e in rounds:  # ±20 min of the round hour
        local = datetime.fromtimestamp(e.ts, TZ)
        minutes = local.hour * 60 + local.minute + local.second / 60
        nearest = round(minutes / 120) * 120
        assert abs(minutes - nearest) <= 25 or abs(minutes - nearest) >= 1440 - 25
    spot = by_type["spot_check"]
    assert len(spot) in (5, 6, 7)  # 2 per day over a 3-day range that starts/ends mid-day
    assert all({"sbp", "dbp", "temp_c"} <= set(e.payload) for e in spot)
    meals = [e for e in by_type["intake_logged"] if e.payload["kind"] == "meal"]
    assert len(meals) >= 8
    staff_ids = {s.staff_id for s in fac.STAFF}
    assert all(e.staff_id in staff_ids for t in ("check_logged", "spot_check", "intake_logged") for e in by_type[t])
    # Bed sensor transitions alternate exit/return.
    bed = [e.type for e in batch.events if e.type in ("bed_exit", "bed_return")]
    assert all(a != b for a, b in zip(bed, bed[1:], strict=False))


def test_variants_afib_beta_blocker_copd() -> None:
    kw = dict(unit="East", room_index=0, mobility="independent", cognition="intact")
    afib = simulate(make_params(SEED, 500, flags={"afib"}, **kw)).cols
    bb = simulate(make_params(SEED, 501, flags={"beta_blocker"}, **kw)).cols
    copd = simulate(make_params(SEED, 502, flags={"copd"}, **kw)).cols
    plain = simulate(make_params(SEED, 503, **kw)).cols
    assert np.nanstd(np.diff(afib["hr"])) > 3 * np.nanstd(np.diff(plain["hr"]))  # irregular rhythm
    assert np.nanmean(afib["hr"]) > 85
    assert np.nanmax(bb["hr"]) <= 95 and np.nanmean(bb["hr"]) < np.nanmean(plain["hr"]) + 5
    assert np.nanmin(copd["spo2"]) >= 88 and np.nanmax(copd["spo2"]) <= 92


def test_live_stepping_one_tick_at_a_time(params) -> None:
    sim = ResidentSim(params[7], SEED, TZ)
    t0 = int(NOW.timestamp()) // 5 * 5
    rows = [sim.step(t0 + 5 * i, 1) for i in range(24)]
    assert all(b.rows == 1 for b in rows)
    hr = np.array([b.cols["hr"][0] for b in rows])
    assert np.nanmax(np.abs(np.diff(hr))) < 15  # AR state carries over: no jumps between ticks


def test_same_seed_same_history(params) -> None:
    a, b = simulate(params[9], days=1), simulate(params[9], days=1)
    for key in ("hr", "rr", "spo2", "motion", "in_bed"):
        np.testing.assert_array_equal(a.cols[key], b.cols[key])
    assert a.events == b.events


def test_tick_by_tick_emits_the_same_staff_events_as_one_big_step(params) -> None:
    """The 12:00 round starts at 11:40, before its noon-to-noon plan begins: 1-tick live steps must not lose it."""
    p = params[3]  # East room 103: checked a couple of minutes into the round
    t0 = int(fac.local_epoch(NOW.astimezone(TZ).date(), 11.5, TZ))
    big = ResidentSim(p, SEED, TZ).step(t0, 720)
    small_sim = ResidentSim(p, SEED, TZ)
    small = [e for i in range(720) for e in small_sim.step(t0 + 5 * i, 1).events]

    def staff(events):
        return sorted((e.ts, e.type) for e in events if e.type not in ("bed_exit", "bed_return"))

    assert any(e.type == "check_logged" and e.ts < t0 + 1800 for e in big.events)  # the 11:40 round check
    assert staff(small) == staff(big.events)


def test_fall_risk_residents_get_up_with_staff(params) -> None:
    """Wake-ups and nap ends of residents on fall precautions follow a staff check, so the fall-prevention
    rule does not fire every morning (or for every nap under force_night)."""
    risky = [p for p in params.values() if p.fall_precautions and p.resident_id not in cast.CAST_IDS][:6]
    assert risky and not any(p.fall_precautions for p in params.values() if p.bedbound)
    for p in risky:
        sim = ResidentSim(p, SEED, TZ)
        for day in range(20370, 20374):
            plan = sim.plan(day)
            checks = [e.ts for e in plan.events if e.type == "check_logged" and e.payload["kind"] == "assist"]
            for got_up in [plan.bed[1]] + ([plan.nap[1]] if plan.nap else []):
                assert any(0 < got_up - c <= 300 for c in checks), (p.resident_id, day)


def test_scenarios_only_touch_their_own_window(params) -> None:
    from app.sim.physiology import Scenario

    class Flatten(Scenario):
        def __init__(self, start, end):
            self.resident_id, self.start, self.end = 7, start, end

        def apply(self, sig):
            sig.cols["hr"] = np.full(len(sig.t), 50.0)

    t0 = int(NOW.timestamp()) // 5 * 5 - 86400
    plain = ResidentSim(params[7], SEED, TZ).step(t0, 1440).cols["hr"]
    flat = ResidentSim(params[7], SEED, TZ, [Flatten(t0 + 3600, t0 + 5400)]).step(t0, 1440).cols["hr"]
    inside = slice(720, 1080)
    assert (flat[inside][~np.isnan(flat[inside])] == 50).all()
    np.testing.assert_array_equal(flat[:720], plain[:720])
    np.testing.assert_array_equal(flat[1080:], plain[1080:])
