"""Roster and the scripted "last night" cast (SPEC §2, §8)."""

from collections import Counter
from datetime import UTC, date, datetime

import numpy as np
import pytest

from app.sim import cast
from app.sim import facility as fac

SEED = 1337
TZ = fac.tzinfo("America/Los_Angeles")
NOW = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)  # 14:00 facility time
NIGHT = fac.scripted_night_date(NOW, TZ)
HISTORY_DAYS = 8


def at(hours: float) -> float:
    """Epoch of `hours` after local midnight of the scripted night's evening date."""
    return fac.local_epoch(NIGHT, hours, TZ)


@pytest.fixture(scope="module")
def roster() -> list[cast.Resident]:
    return cast.build_roster(SEED)


@pytest.fixture(scope="module")
def history(roster):
    """HISTORY_DAYS of history ending NOW for the cast, with the scripted night applied."""
    sims = cast.build_sims(SEED, TZ, roster, NIGHT)
    t0 = int(NOW.timestamp()) // 5 * 5 - HISTORY_DAYS * 86400
    out = {}
    for rid in cast.CAST_IDS:
        batch = sims[rid].step(t0, HISTORY_DAYS * 17280)
        out[rid] = (batch.cols, batch.events, batch.cols["ts"] / 1e6)
    return out


def window_mean(cols, ts, key, hours, minutes=2.5):
    sel = (ts >= at(hours) - minutes * 60) & (ts < at(hours) + minutes * 60)
    return float(np.nanmean(cols[key][sel]))


def minute_means(values: np.ndarray, ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    minute = (ts // 60).astype(np.int64)
    keys, inv = np.unique(minute, return_inverse=True)
    ok = ~np.isnan(values)
    sums = np.bincount(inv, weights=np.where(ok, values, 0), minlength=len(keys))
    counts = np.bincount(inv, weights=ok, minlength=len(keys))
    with np.errstate(invalid="ignore", divide="ignore"):
        return keys * 60.0, sums / counts


def night_block_p95(cols, ts, key="hr") -> float:
    """Her own baseline the way the Tiger job computes it: p95 of 1-minute averages, 00:00–04:00 local,
    with the scripted night held out."""
    mts, means = minute_means(cols[key], ts)
    t = mts.astype(np.int64)
    hours = fac.local_hours(t, fac.utc_offsets(t, TZ))
    window = cast.scripted_window(NIGHT, TZ)
    held_out = (mts >= window["holdout_start"]) & (mts < window["holdout_end"])
    sel = (hours < 4) & ~held_out & ~np.isnan(means)
    return float(np.percentile(means[sel], 95))


# --------------------------------------------------------------------------- roster


def test_roster_has_120_residents_40_per_unit(roster) -> None:
    assert [r.resident_id for r in roster] == list(range(1, 121))
    assert Counter(r.unit for r in roster) == {"East": 40, "West": 40, "Memory Care": 40}
    rooms = [r.room for r in roster]
    assert len(set(rooms)) == 120
    for r in roster:
        base = fac.UNIT_ROOM_BASE[r.unit]
        assert base <= int(r.room) < base + 40
    assert len({r.name for r in roster}) == 120
    assert [r.resident_id for r in roster if r.is_live] == [120]


def test_roster_attributes_are_valid(roster) -> None:
    for r in roster:
        assert 65 <= r.age <= 101
        assert r.mobility in ("independent", "1-assist", "2-assist", "bedbound")
        assert r.cognition in ("intact", "mild", "moderate", "severe")
        assert r.code_status in ("FULL", "DNR", "DNR-DNI", "COMFORT")
        assert 0 <= r.morse <= 125
        assert r.diagnoses and r.emergency_contact["phone"].startswith("(415) 555-01")
        assert set(r.flags) <= {"beta_blocker", "afib", "copd", "insulin", "opioid"}
        if r.unit == "Memory Care":
            assert r.cognition != "intact"


def test_cast_matches_spec(roster) -> None:
    by_id = {r.resident_id: r for r in roster}
    alvarez, lee, patel, ortiz = by_id[17], by_id[42], by_id[101], by_id[110]
    kim, rossi, wright, grant = by_id[88], by_id[91], by_id[33], by_id[120]
    assert (alvarez.name, alvarez.age, alvarez.unit, alvarez.cognition) == (
        "Rosa Alvarez",
        87,
        "Memory Care",
        "moderate",
    )
    assert (alvarez.code_status, alvarez.morse) == ("FULL", 55)
    assert (lee.name, lee.age, lee.code_status) == ("Henry Lee", 91, "DNR") and "beta_blocker" in lee.flags
    assert any("Metoprolol" in m.name for m in lee.medications)
    assert (patel.name, patel.age, patel.code_status) == ("Arthur Patel", 94, "COMFORT") and patel.profile["hospice"]
    assert (ortiz.name, ortiz.age, ortiz.morse, ortiz.mobility) == ("James Ortiz", 82, 75, "1-assist")
    assert (kim.name, kim.age) == ("Margaret Kim", 79) and "copd" in kim.flags
    assert (rossi.name, rossi.age) == ("Elena Rossi", 84) and "afib" in rossi.flags
    assert (wright.name, wright.age) == ("Doris Wright", 80)
    assert (grant.name, grant.age, grant.morse, grant.mobility, grant.code_status) == (
        "Thomas Grant", 78, 70, "1-assist", "FULL",
    )  # fmt: skip
    assert grant.is_live and "bed alarm" in grant.profile["fall_precautions"]


def test_scripted_night_is_the_last_completed_night() -> None:
    assert fac.scripted_night_date(datetime(2026, 10, 16, 14, 0, tzinfo=UTC), TZ) == date(2026, 10, 15)  # 07:00 PDT
    assert fac.scripted_night_date(datetime(2026, 10, 16, 12, 0, tzinfo=UTC), TZ) == date(2026, 10, 14)  # 05:00 PDT
    window = cast.scripted_window(NIGHT, TZ)
    assert window["night_end"] <= NOW.timestamp()  # never a future timestamp


# --------------------------------------------------------------------------- Alvarez (17)


def test_alvarez_hr_at_0200_above_her_own_p95_but_below_100(history) -> None:
    cols, _, ts = history[17]
    p95 = night_block_p95(cols, ts)
    hr_0200 = window_mean(cols, ts, "hr", 26.0)
    assert hr_0200 > p95 + 10, (hr_0200, p95)
    assert hr_0200 < 100
    assert 84 <= window_mean(cols, ts, "hr", 26 + 14 / 60) <= 92  # ≈ 88 at 02:14


def test_alvarez_no_fixed_threshold_before_0350(history) -> None:
    cols, _, ts = history[17]
    before = (ts >= at(12)) & (ts < at(27 + 50 / 60))
    assert np.nanmax(cols["hr"][before]) <= 100
    assert np.nanmin(cols["hr"][before]) >= 50
    assert np.nanmax(cols["rr"][before]) <= 24
    assert np.nanmin(cols["rr"][before]) >= 10
    assert np.nanmin(cols["spo2"][before]) >= 90
    # …and the first fixed-threshold alarm (HR > 100) does come shortly after 03:50.
    after = (ts >= at(27 + 50 / 60)) & (ts < at(28.5))
    first = ts[after & (cols["hr"] > 100)]
    assert len(first) and first[0] < at(28 + 5 / 60)


def test_alvarez_drift_shape(history) -> None:
    cols, events, ts = history[17]
    assert window_mean(cols, ts, "rr", 26 + 14 / 60, 7.5) >= 20.5  # RR 16 → ~22
    skin_now = window_mean(cols, ts, "skin_temp", 26 + 14 / 60, 15)
    skin_prior = float(np.nanmean(cols["skin_temp"][(ts >= at(2)) & (ts < at(2.5))]))  # same time, night before
    assert 0.5 <= skin_now - skin_prior <= 1.1  # ≈ +0.8 °C
    night = (ts >= at(22)) & (ts < at(30.5))
    exits = [e for e in events if e.type == "bed_exit" and at(22) <= e.ts < at(30.5)]
    assert len(exits) == 5
    prior_exits = [e for e in events if e.type == "bed_exit" and at(22 - 24) <= e.ts < at(30.5 - 24)]
    assert len(prior_exits) <= 2
    motion_tonight = np.nanmean(cols["motion"][night & (cols["in_bed"] == 1)])
    prior = (ts >= at(22 - 24)) & (ts < at(30.5 - 24)) & (cols["in_bed"] == 1)
    assert motion_tonight > 2 * np.nanmean(cols["motion"][prior])  # restless (×3 movement)
    note = [e for e in events if e.type == "note_added" and abs(e.ts - at(21.5)) < 1]
    assert note and "more confused than usual, only drank half her fluids" in note[0].payload["text"]
    assert note[0].payload["author_role"] == "CNA"


# --------------------------------------------------------------------------- the rest of the cast


def test_lee_fluid_overload_masked_by_beta_blocker(history) -> None:
    cols, _, ts = history[42]
    assert window_mean(cols, ts, "rr", 22.0, 10) <= 18.5
    assert 25 <= window_mean(cols, ts, "rr", 26.5, 5) <= 27.5  # RR ≈ 26 by 02:30
    assert 89 <= window_mean(cols, ts, "spo2", 26.5, 10) <= 91  # SpO2 95 → 90
    hr_rise = window_mean(cols, ts, "hr", 26.5, 10) - window_mean(cols, ts, "hr", 22.5, 10)
    assert 2 <= hr_rise <= 9  # only ~+6 bpm (masked)
    assert np.nanmax(cols["rr"][(ts >= at(21)) & (ts < at(30.5))]) < 28  # never the RR ≥ 28 critical rule


def test_patel_stops_moving_and_slows(history) -> None:
    cols, events, ts = history[101]
    mts, motion = minute_means(np.nan_to_num(cols["motion"]) * 12, ts)  # per-minute sum
    still = (mts >= at(26.5)) & (mts < at(28.5))
    assert np.max(motion[still]) < 6  # no turning 02:30–04:30
    assert window_mean(cols, ts, "hr", 28.5, 5) <= 48  # HR 64 → 46
    assert window_mean(cols, ts, "rr", 28.5, 5) <= 8.6  # RR 12 → 8
    assert window_mean(cols, ts, "hr", 26.5, 10) >= 58
    note = [e for e in events if e.type == "note_added" and at(28) <= e.ts < at(29)]
    assert note and "POLST" in note[0].payload["text"]


def test_ortiz_fall(history) -> None:
    cols, events, ts = history[110]
    impact_at = at(28 + 6.25 / 60)
    i = int(np.argmin(np.abs(ts - impact_at)))
    assert cols["impact"][i] >= 3.5
    after = (ts > impact_at) & (ts <= impact_at + 60)
    assert (cols["in_bed"][after] == 0).all() and np.nanmax(cols["motion"][after]) < 1  # still, on the floor
    assert any(e.type == "check_logged" and e.payload.get("kind") == "fall_response" for e in events)
    exits = [e for e in events if e.type == "bed_exit" and at(28) <= e.ts < at(28.2)]
    assert exits and abs(exits[0].ts - at(28 + 5 / 60)) < 6


def test_wright_band_removed_0100_to_0500(history) -> None:
    cols, _, ts = history[33]
    off = (ts >= at(25)) & (ts < at(29))
    assert (cols["worn"][off] == 0).all() and np.isnan(cols["hr"][off]).all()
    assert (cols["worn"][(ts >= at(24.9)) & (ts < at(25))] == 1).all()
    assert (cols["worn"][(ts >= at(29)) & (ts < at(29.1))] == 1).all()
    assert (cols["in_bed"][off] == 1).mean() > 0.9  # bed sensor keeps reporting


def test_kim_and_rossi_stay_in_their_personal_ranges(history) -> None:
    kim, _, _ = history[88]
    rossi, _, _ = history[91]
    assert np.nanmin(kim["spo2"]) >= 88 and np.nanmax(kim["spo2"]) <= 91  # COPD, Scale 2
    assert np.nanmin(rossi["hr"]) >= 85 and np.nanmax(rossi["hr"]) <= 118  # AFib


def test_quiet_night_for_everyone_else(roster) -> None:
    """Except Alvarez and Ortiz, last night's bathroom trips are call-light assisted (a check right before),
    including cast members without a trip script such as Grant (R-120, Morse 70)."""
    sims = cast.build_sims(SEED, TZ, roster, NIGHT)
    for rid in (5, 20, 60, 100, 120, 42):
        plan = sims[rid].plan(NIGHT.toordinal() - date(1970, 1, 1).toordinal())
        assert all(trip.assisted for trip in plan.trips)
        assists = [e for e in plan.events if e.payload.get("reason") == cast.BATHROOM]
        assert len(assists) == len(plan.trips)
        assert not [e for e in plan.events if e.type == "note_added" and e.payload.get("routine")]


def test_bookmarks_are_in_the_scripted_night() -> None:
    marks = cast.bookmarks(NIGHT, TZ)
    assert [m["label"] for m in marks][2] == "Alvarez reaches #1"
    window = cast.scripted_window(NIGHT, TZ)
    for m in marks:
        t = datetime.fromisoformat(m["ts"]).timestamp()
        assert window["night_start"] <= t < window["night_end"]
    local = datetime.fromisoformat(marks[2]["ts"]).astimezone(TZ)
    assert (local.hour, local.minute) == (2, 14)
