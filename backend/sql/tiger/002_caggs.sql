-- 002_caggs.sql: hierarchical continuous aggregates vitals_1m → vitals_15m → vitals_1h (SPEC §6).
-- Real-time aggregation is on (materialized_only = false) so the newest, not-yet-materialized
-- buckets are still returned. Column names match the Snowflake VITALS_15M features (HR_AVG, ...).

CREATE MATERIALIZED VIEW IF NOT EXISTS vitals_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket,
       resident_id,
       avg(hr)                  AS hr_avg,
       min(hr)                  AS hr_min,
       max(hr)                  AS hr_max,
       stats_agg(hr)            AS hr_stats,      -- toolkit: exact mean/stddev that rolls up
       avg(hrv_rmssd)           AS hrv_avg,
       avg(rr)                  AS rr_avg,
       min(rr)                  AS rr_min,
       max(rr)                  AS rr_max,
       min(spo2)                AS spo2_min,
       avg(skin_temp)           AS skin_temp_avg,
       sum(motion)              AS motion_sum,
       max(impact)              AS impact_max,
       avg(in_bed::int)::real   AS in_bed_frac,
       avg(bed_motion)          AS bed_motion_avg,
       avg(worn::int)::real     AS worn_frac,
       count(*)                 AS n
FROM vitals
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS vitals_15m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '15 minutes', bucket) AS bucket,
       resident_id,
       avg(hr_avg)              AS hr_avg,
       min(hr_min)              AS hr_min,
       max(hr_max)              AS hr_max,
       rollup(hr_stats)         AS hr_stats,
       percentile_agg(hr_avg)   AS hr_pct,        -- distribution of 1-min HR; feeds baselines via rollup
       avg(hrv_avg)             AS hrv_avg,
       avg(rr_avg)              AS rr_avg,
       min(rr_min)              AS rr_min,
       max(rr_max)              AS rr_max,
       min(spo2_min)            AS spo2_min,
       avg(skin_temp_avg)       AS skin_temp_avg,
       sum(motion_sum)          AS motion_sum,
       max(impact_max)          AS impact_max,
       avg(in_bed_frac)::real   AS in_bed_frac,
       avg(bed_motion_avg)      AS bed_motion_avg,
       avg(worn_frac)::real     AS worn_frac,
       sum(n)                   AS n
FROM vitals_1m
GROUP BY 1, 2
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS vitals_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', bucket) AS bucket,
       resident_id,
       avg(hr_avg)              AS hr_avg,
       min(hr_min)              AS hr_min,
       max(hr_max)              AS hr_max,
       rollup(hr_stats)         AS hr_stats,
       rollup(hr_pct)           AS hr_pct,
       avg(hrv_avg)             AS hrv_avg,
       avg(rr_avg)              AS rr_avg,
       min(rr_min)              AS rr_min,
       max(rr_max)              AS rr_max,
       min(spo2_min)            AS spo2_min,
       avg(skin_temp_avg)       AS skin_temp_avg,
       sum(motion_sum)          AS motion_sum,
       max(impact_max)          AS impact_max,
       avg(in_bed_frac)::real   AS in_bed_frac,
       avg(bed_motion_avg)      AS bed_motion_avg,
       avg(worn_frac)::real     AS worn_frac,
       sum(n)                   AS n
FROM vitals_15m
GROUP BY 1, 2
WITH NO DATA;
