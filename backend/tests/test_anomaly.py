"""Snowflake deep tier, pure parts: severity mapping, DETECT_ANOMALIES parsing, P_anom from Snowflake results
(with the Tiger z fallback), and the sync's DataFrame builders and cutoffs. No database."""

from datetime import UTC, datetime, timedelta
from statistics import NormalDist

import pytest

from app.deep import anomaly, sync
from app.scoring import priority
from tests.scoring_helpers import NIGHT, baselines, clock, features, profile

COLS = ["SERIES", "TS", "Y", "FORECAST", "LOWER_BOUND", "UPPER_BOUND", "IS_ANOMALY", "PERCENTILE", "DISTANCE"]
BUCKET = datetime(2026, 10, 16, 9, 0)  # naive UTC, as DETECT_ANOMALIES returns it


# --------------------------------------------------------------------------- severity


def test_severity_is_zero_inside_the_99_interval_and_one_at_z_full() -> None:
    assert pytest.approx(2.5758, abs=1e-4) == anomaly.Z_INTERVAL
    assert anomaly.severity(0.0, "hr") == 0
    assert anomaly.severity(2.5, "hr") == 0
    assert anomaly.severity(-2.57, "hr") == 0
    assert anomaly.severity(5.0, "hr") == pytest.approx(1)
    assert anomaly.severity(9.0, "hr") == 1
    mid = (anomaly.Z_INTERVAL + anomaly.Z_FULL) / 2
    assert anomaly.severity(mid, "rr") == pytest.approx(0.5)
    assert anomaly.severity(-mid, "rr") == pytest.approx(0.5)  # both sides matter for HR/RR/skin temp
    assert anomaly.severity(None, "hr") == 0


def test_severity_is_one_sided_for_spo2_and_motion() -> None:
    assert anomaly.severity(6.0, "spo2") == 0  # SpO₂ above expected is fine
    assert anomaly.severity(-6.0, "spo2") == 1
    assert anomaly.severity(-6.0, "motion") == 0  # less movement than expected is the stillness rule's job
    assert anomaly.severity(6.0, "motion") == 1


def test_z_comes_from_percentile_and_falls_back_to_distance() -> None:
    assert anomaly.signed_z(0.5, None) == pytest.approx(0)
    assert anomaly.signed_z(NormalDist().cdf(3.0), 99.0) == pytest.approx(3.0)
    assert anomaly.signed_z(8.3e-15, -7.67) == pytest.approx(-7.67, abs=0.01)
    assert anomaly.signed_z(1.0, 11.2) == 11.2  # percentile saturated
    assert anomaly.signed_z(0.0, -12.0) == -12.0
    assert anomaly.signed_z(None, None) is None


def test_combined_is_one_minus_product() -> None:
    assert anomaly.combined([]) == 0
    assert anomaly.combined([0.5, 0.5]) == pytest.approx(0.75)
    assert anomaly.combined([1.0, 0.2]) == 1


def test_series_id_parses_variant_text() -> None:
    assert [anomaly.series_id(v) for v in ("17", '"17"', 17, 17.0)] == [17, 17, 17, 17]


# --------------------------------------------------------------------------- parsing + payloads


def _row(rid, metric_z: float, y=80.0, forecast=62.0, ts=BUCKET):
    pct = NormalDist().cdf(metric_z)
    return (str(rid), ts, y, forecast, 58.0, 66.0, abs(metric_z) > anomaly.Z_INTERVAL, pct, metric_z)


def test_parse_rows_and_build_payloads() -> None:
    hr = anomaly.parse_rows("hr", COLS, [_row(17, 4.0), _row(91, 1.0)])
    skin = anomaly.parse_rows("skin_temp", COLS, [_row(17, 6.0, y=34.9, forecast=34.0)])
    assert hr[0]["resident_id"] == 17 and hr[0]["bucket"] == BUCKET.replace(tzinfo=UTC)
    assert hr[0]["z"] == pytest.approx(4.0, abs=1e-3) and 0 < hr[0]["severity"] < 1
    assert hr[1]["severity"] == 0 and hr[1]["is_anomaly"] is False

    payloads = anomaly.build_payloads(hr + skin)
    p17 = payloads[(17, BUCKET.replace(tzinfo=UTC))]
    assert set(p17["metrics"]) == {"hr", "skin_temp"}
    assert p17["anomalous"] == ["hr", "skin_temp"]
    assert p17["p_anom"] == pytest.approx(1.0)  # skin temp at z 6 is severity 1
    assert p17["metrics"]["hr"]["lower"] == 58.0 and p17["metrics"]["hr"]["upper"] == 66.0
    assert payloads[(91, BUCKET.replace(tzinfo=UTC))]["anomalous"] == []


def test_results_frame_has_the_table_columns() -> None:
    df = anomaly.results_frame(anomaly.parse_rows("hr", COLS, [_row(17, 4.0)]), datetime(2026, 10, 16, 10, tzinfo=UTC))
    assert list(df.columns) == [
        "RESIDENT_ID", "METRIC", "BUCKET_TS", "Y", "FORECAST", "LOWER_BOUND", "UPPER_BOUND", "IS_ANOMALY",
        "PERCENTILE", "DISTANCE", "SEVERITY", "MODEL", "DETECTED_AT",
    ]  # fmt: skip
    assert df["MODEL"][0] == "ML.AD_HR" and df["BUCKET_TS"][0].tzinfo is None


def test_detect_sql_bounds_and_config() -> None:
    sql = anomaly.detect_sql("spo2", datetime(2026, 10, 16, 5, tzinfo=UTC), datetime(2026, 10, 16, 13, 30, tzinfo=UTC))
    assert sql.startswith("CALL ML.AD_SPO2!DETECT_ANOMALIES(")
    assert "BUCKET_TS >= ''2026-10-16 05:00:00''::TIMESTAMP_NTZ" in sql
    assert "BUCKET_TS < ''2026-10-16 13:30:00''::TIMESTAMP_NTZ" in sql
    assert "SELECT RESIDENT_ID, BUCKET_TS, SPO2_MIN, IN_BED_FRAC FROM CORE.VITALS_15M" in sql
    assert "TARGET_COLNAME => 'SPO2_MIN'" in sql and "{'prediction_interval': 0.99}" in sql


# --------------------------------------------------------------------------- P_anom from Snowflake


def _insight(as_of: datetime, **metrics) -> dict:
    """ai_insights row as the scoring query returns it; metrics: name=(y, forecast, lower, upper, z)."""
    m = {}
    for name, (y, f, lo, hi, z) in metrics.items():
        m[name] = {"y": y, "forecast": f, "lower": lo, "upper": hi, "z": z, "severity": anomaly.severity(z, name),
                   "is_anomaly": abs(z) > anomaly.Z_INTERVAL}  # fmt: skip
    return {"as_of": as_of, "payload": {"bucket": (as_of - timedelta(minutes=20)).isoformat(), "metrics": m}}


def test_snowflake_results_replace_the_tiger_z_fallback() -> None:
    # Tiger windows say "very abnormal", Snowflake says "within expected": Snowflake wins when fresh.
    hot = {"hr": 90.0, "rr": 22.0, "spo2": 96.0, "skin_temp": 35.0, "motion": 2.0, "in_bed": 1.0}
    calm = _insight(NIGHT - timedelta(minutes=8), hr=(64, 63, 58, 68, 0.4), rr=(15, 15, 13, 17, 0.0))
    f = features(w0=hot, w1=hot, anomaly=[calm])
    r = priority.compute(profile(), f, baselines(), clock())
    assert r.components.anom_source == "snowflake"
    assert r.components.p_anom == 0
    assert r.components.anom_as_of == calm["as_of"].isoformat()

    tiger = priority.compute(profile(), features(w0=hot, w1=hot), baselines(), clock())
    assert tiger.components.anom_source == "tiger_z" and tiger.components.p_anom > 0.9


def test_p_anom_uses_the_worse_of_the_last_two_buckets() -> None:
    newest = _insight(NIGHT - timedelta(minutes=8), hr=(70, 63, 58, 68, 2.0))
    older = _insight(NIGHT - timedelta(minutes=23), hr=(80, 63, 58, 68, 5.0), skin_temp=(34.8, 34.0, 33.8, 34.2, 0.5))
    r = priority.compute(profile(), features(anomaly=[newest, older]), baselines(), clock())
    dev = r.deviations["hr"]
    assert dev.source == "snowflake" and dev.window == 1 and dev.severity == pytest.approx(1)
    assert r.components.p_anom == pytest.approx(1)
    assert r.components.anomalous_metrics == ["hr"]


def test_multi_signal_counts_snowflake_flags() -> None:
    one = _insight(
        NIGHT - timedelta(minutes=8),
        hr=(84, 62, 57, 67, 3.0), rr=(21, 16, 14, 18, 3.0), skin_temp=(34.9, 34.0, 33.8, 34.2, 3.0),
        motion=(30, 5, 0, 12, 1.0),
    )  # fmt: skip
    r = priority.compute(profile(), features(anomaly=[one]), baselines(), clock())
    assert sorted(r.components.anomalous_metrics) == ["hr", "rr", "skin_temp"]
    assert r.components.multi_signal is True
    expected = anomaly.combined(anomaly.severity(3.0, m) for m in ("hr", "rr", "skin_temp"))
    assert r.components.p_anom == pytest.approx(expected)


def test_snowflake_factor_text_names_the_expected_range() -> None:
    one = _insight(NIGHT - timedelta(minutes=8), hr=(86, 61, 56, 66, 7.0), skin_temp=(34.9, 34.1, 33.9, 34.3, 6.0))
    r = priority.compute(profile(sex="F"), features(anomaly=[one]), baselines(), clock())
    texts = [x.text for x in r.factors]
    assert "HR 86 (her expected 56–66)" in texts
    assert "Skin temp +0.8 °C vs her expected" in texts


def test_snowflake_results_without_values_score_nothing() -> None:
    empty = {"as_of": NIGHT, "payload": {"bucket": NIGHT.isoformat(), "metrics": {"hr": {"y": None, "severity": 0}}}}
    r = priority.compute(profile(), features(anomaly=[empty]), baselines(), clock())
    assert r.components.anom_source == "snowflake" and r.components.p_anom == 0 and r.deviations == {}


# --------------------------------------------------------------------------- sync builders


def test_sync_cutoffs_trail_the_data_frontier() -> None:
    now = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
    vit, ev = sync.sync_cutoffs(now, now - timedelta(seconds=4))
    assert vit == now - timedelta(seconds=4) - sync.BUCKET_MARGIN
    assert ev == now - timedelta(seconds=4) - sync.EVENT_MARGIN
    behind = now - timedelta(hours=2)  # catch-up backfill in progress: only what is already written
    assert sync.sync_cutoffs(now, behind)[0] == behind - sync.BUCKET_MARGIN
    assert sync.sync_cutoffs(now, None)[0] == now - sync.BUCKET_MARGIN


def test_vitals_frame_is_naive_utc_and_upper_case() -> None:
    row = {k: 1.0 for k in sync.VITALS_COLUMNS} | {
        "resident_id": 17,
        "bucket": datetime(2026, 10, 16, 2, 0, tzinfo=sync.UTC).astimezone(),
        "n": 180,
        "rr_avg": None,
    }
    df = sync.vitals_frame([row], datetime(2026, 10, 16, 3, tzinfo=UTC))
    assert list(df.columns) == [*sync.VITALS_COLUMNS.values(), "SYNCED_AT"]
    assert df["BUCKET_TS"][0] == datetime(2026, 10, 16, 2, 0) and df["BUCKET_TS"].dt.tz is None
    assert df["RR_AVG"].isna()[0] and df["N"][0] == 180


def test_event_frames_split_into_typed_tables() -> None:
    ts = datetime(2026, 10, 16, 4, 30, tzinfo=UTC)
    rows = [
        {"ts": ts, "resident_id": 17, "type": "note_added", "staff_id": 3,
         "payload": {"text": "More confused than usual", "author_role": "CNA", "source": "text"}},
        {"ts": ts, "resident_id": 17, "type": "check_logged", "staff_id": None, "payload": {"kind": "bedside"}},
        {"ts": ts, "resident_id": 5, "type": "alert_raised", "staff_id": None,
         "payload": {"tier": "CRITICAL", "from_tier": "STABLE", "score": 100, "rule": "fall", "factors": ["Fall"]}},
        {"ts": ts, "resident_id": 5, "type": "bed_exit", "staff_id": None, "payload": {}},
    ]  # fmt: skip
    frames = sync.event_frames(rows, ts)
    assert len(frames["EVENTS"]) == 4
    assert frames["NURSE_NOTES"]["TEXT"][0] == "More confused than usual"
    assert frames["CHECKS"]["KIND"][0] == "bedside" and frames["CHECKS"]["SOURCE"][0] == "sim"
    assert frames["CHECKS"]["STAFF_ID"].isna()[0]
    a = frames["ALERTS"].iloc[0]
    assert (a["EVENT"], a["TIER"], a["RULE"], a["FACTORS"]) == ("raised", "CRITICAL", "fall", '["Fall"]')
    assert frames["EVENTS"]["PAYLOAD"][3] == "{}"
    assert "STAFF_ID" not in frames["ALERTS"]


def test_detection_gets_lead_in_context_but_keeps_only_the_requested_range(monkeypatch) -> None:
    seen = []

    def fake(conn, sql, params=None, *, feature="sql"):
        seen.append(sql)
        rows = [_row(17, 4.0, ts=datetime(2026, 10, 16, h, 0)) for h in (4, 5, 6)]
        return COLS, rows

    monkeypatch.setattr(anomaly.sf, "query_columns", fake)
    start, end = datetime(2026, 10, 16, 5, tzinfo=UTC), datetime(2026, 10, 16, 7, tzinfo=UTC)
    det = anomaly.run_detection(None, start, end, input_start=start - timedelta(hours=24), metrics=("hr",))
    assert "BUCKET_TS >= ''2026-10-15 05:00:00''" in seen[0]
    assert [r["bucket"].hour for r in det.results] == [5, 6] and det.ok


def test_motion_detection_reads_the_night_in_bed_view() -> None:
    start, end = datetime(2026, 10, 16, 5, tzinfo=UTC), datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
    assert "FROM ML.MOTION_NIGHT_IN_BED WHERE" in anomaly.detect_sql("motion", start, end)
    assert "FROM CORE.VITALS_15M WHERE" in anomaly.detect_sql("hr", start, end)
