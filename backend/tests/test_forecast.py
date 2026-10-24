"""Snowflake FORECAST plumbing (SPEC §9 ③): data windows, SQL, parsing and the replay plan (no Snowflake)."""

from datetime import UTC, datetime, timedelta

from app.deep import forecast

T = datetime(2026, 10, 17, 8, 8, tzinfo=UTC)


def test_data_end_is_the_newest_bucket_snowflake_has() -> None:
    assert forecast.data_end_for(T) == datetime(2026, 10, 17, 8, 0, tzinfo=UTC)  # 07:45 bucket synced at ~08:05
    assert forecast.data_end_for(datetime(2026, 10, 17, 8, 4, tzinfo=UTC)) == datetime(2026, 10, 17, 7, 45, tzinfo=UTC)


def test_sql_is_single_series_with_evaluation_off() -> None:
    sql = forecast.create_sql("ML.FC_X", 42, "RR_AVG", T - timedelta(hours=12), T)
    assert "SNOWFLAKE.ML.FORECAST ML.FC_X" in sql and "RESIDENT_ID = 42" in sql and "SERIES_COLNAME" not in sql
    assert "'evaluate': FALSE" in sql and "''2026-10-17 08:08:00''::TIMESTAMP_NTZ" in sql
    assert "FORECASTING_PERIODS => 16" in forecast.forecast_sql("ML.FC_X")


def test_parse_forecast_sorts_and_rounds() -> None:
    cols = ["SERIES", "TS", "FORECAST", "LOWER_BOUND", "UPPER_BOUND"]
    rows = [
        (None, datetime(2026, 10, 17, 8, 15), 21.04, 20.5, 21.6),
        (None, datetime(2026, 10, 17, 8, 0), 20.666, 20.1, 21.2),
    ]
    assert forecast.parse_forecast(cols, rows) == [
        ["2026-10-17T08:00:00+00:00", 20.67, 20.1, 21.2], ["2026-10-17T08:15:00+00:00", 21.04, 20.5, 21.6]]  # fmt: skip


def _iso(h: int, m: int) -> str:
    day = 16 if h >= 12 else 17
    return datetime(2026, 10, day, h, m, tzinfo=UTC).isoformat()


def test_replay_plan_asks_like_the_live_forecaster() -> None:
    # Lee-like: brief HIGHs, then a long one. Ask on entry unless the last ask is < 30 min old, then every 30 min.
    tr = {42: [[_iso(6, 0), "STABLE", 20], [_iso(6, 48), "HIGH", 60], [_iso(6, 52), "WATCH", 41],
               [_iso(7, 34), "HIGH", 60], [_iso(7, 36), "WATCH", 38], [_iso(8, 8), "HIGH", 60],
               [_iso(9, 20), "WATCH", 55]]}  # fmt: skip
    end = datetime(2026, 10, 17, 13, 30, tzinfo=UTC)
    plan = forecast.plan_replay(tr, end)
    assert [t.strftime("%H:%M") for _, t in plan] == ["06:48", "07:34", "08:08", "08:38", "09:08"]


def test_replay_plan_keeps_the_cast_when_capped() -> None:
    tr = {5: [[_iso(6, 0), "HIGH", 70]], 42: [[_iso(8, 0), "HIGH", 60]]}
    end = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    plan = forecast.plan_replay(tr, end, cast={42}, cap=2)
    assert [rid for rid, _ in plan] == [42, 42]
