-- 004_jobs.sql: the hourly personal-baseline job (SPEC §6), scheduled in Tiger with add_job.
--
-- refresh_resident_baselines(as_of) learns each resident's "normal" from the trailing
-- `window_days` ending `exclude_recent` before as_of:
--   * per 4-hour block of facility local time: p05/p50/p95/mean/stddev of hr, rr, spo2, skin_temp, motion,
--     all over 1-minute values. HR percentiles roll up the vitals_15m percentile_agg sketches.
--   * per night (22:00–06:00, tod_block -1): bed exits and the longest still period (state_agg),
--     summarized over nights (the fast tier reads night_bed_exits_p95 and still_minutes_p95).
-- The `baseline_holdout` range in sim_meta (the scripted "last night") is left out so replay is
-- scored against a baseline that never saw it. as_of is a parameter; only the job wrapper uses now().

CREATE OR REPLACE FUNCTION refresh_resident_baselines(
    as_of          timestamptz,
    window_days    int      DEFAULT 7,
    exclude_recent interval DEFAULT INTERVAL '6 hours'
) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    tz           text := coalesce((SELECT value ->> 'tz' FROM sim_meta WHERE key = 'facility'), 'America/Los_Angeles');
    win_start    timestamptz := as_of - make_interval(days => window_days);
    win_end      timestamptz := as_of - exclude_recent;
    hold_start   timestamptz;
    hold_end     timestamptz;
    still_motion constant double precision := 6;  -- wrist motion per minute below this counts as "still"
    n_rows       bigint;
BEGIN
    SELECT (value ->> 'start')::timestamptz, (value ->> 'end')::timestamptz
      INTO hold_start, hold_end
      FROM sim_meta WHERE key = 'baseline_holdout';
    hold_start := coalesce(hold_start, 'infinity');
    hold_end := coalesce(hold_end, 'infinity');

    -- Dynamic SQL with literal bounds, so chunk exclusion happens at plan time.
    -- %1$L win_start · %2$L win_end · %3$L hold_start · %4$L hold_end · %5$L tz · %6$s still_motion · %7$L as_of

    -- Results are staged in a temp table: CREATE TABLE AS may use parallel workers, INSERT … SELECT may not
    -- (about 6x faster here). The staged rows are then upserted in one statement.
    DROP TABLE IF EXISTS pg_temp._baseline_new, pg_temp._baseline_behavior;

    -- ------------------------------------------------------------ vital signs by time-of-day block
    EXECUTE format($sql$
    CREATE TEMP TABLE _baseline_new AS
    WITH hr AS (
        SELECT resident_id,
               (extract(hour FROM bucket AT TIME ZONE %5$L)::int / 4)::smallint AS tod_block,
               rollup(hr_pct)   AS pct
        FROM vitals_15m
        WHERE bucket >= %1$L AND bucket + INTERVAL '15 minutes' <= %2$L
          AND NOT (bucket + INTERVAL '15 minutes' > %3$L AND bucket < %4$L)
          AND hr_pct IS NOT NULL
        GROUP BY 1, 2
    ), by_minute AS (
        SELECT resident_id,
               (extract(hour FROM bucket AT TIME ZONE %5$L)::int / 4)::smallint AS tod_block,
               stats_agg(hr_avg)             AS hr_s,
               percentile_agg(rr_avg)        AS rr_p,     stats_agg(rr_avg)        AS rr_s,
               percentile_agg(spo2_min)      AS spo2_p,   stats_agg(spo2_min)      AS spo2_s,
               percentile_agg(skin_temp_avg) AS skin_p,   stats_agg(skin_temp_avg) AS skin_s,
               percentile_agg(motion_sum)    AS motion_p, stats_agg(motion_sum)    AS motion_s
        FROM vitals_1m
        WHERE bucket >= %1$L AND bucket + INTERVAL '1 minute' <= %2$L
          AND NOT (bucket + INTERVAL '1 minute' > %3$L AND bucket < %4$L)
        GROUP BY 1, 2
    ), long_form AS (
        -- HR percentiles roll up the vitals_15m sketches; mean/stddev use the same 1-minute averages.
        SELECT h.resident_id, h.tod_block, 'hr'::text AS metric, h.pct, m.hr_s AS st
        FROM hr h JOIN by_minute m USING (resident_id, tod_block)
        UNION ALL
        SELECT m.resident_id, m.tod_block, v.metric, v.pct, v.st
        FROM by_minute m
        CROSS JOIN LATERAL (VALUES ('rr', m.rr_p, m.rr_s),
                                   ('spo2', m.spo2_p, m.spo2_s),
                                   ('skin_temp', m.skin_p, m.skin_s),
                                   ('motion', m.motion_p, m.motion_s)) AS v (metric, pct, st)
    )
    SELECT resident_id, tod_block, metric,
           approx_percentile(0.05, pct) AS p05, approx_percentile(0.50, pct) AS p50,
           approx_percentile(0.95, pct) AS p95, average(st) AS mean, stddev(st) AS stddev, num_vals(st) AS n, %7$L::timestamptz AS updated_at
    FROM long_form
    WHERE pct IS NOT NULL AND num_vals(st) > 0
    $sql$, win_start, win_end, hold_start, hold_end, tz, still_motion, as_of);

    -- ------------------------------------------------------------ per-night behavior (tod_block -1)
    EXECUTE format($sql$
    CREATE TEMP TABLE _baseline_behavior AS
    WITH night_minutes AS (
        SELECT resident_id, bucket, motion_sum,
               ((bucket AT TIME ZONE %5$L) - INTERVAL '12 hours')::date AS night
        FROM vitals_1m
        WHERE bucket >= %1$L AND bucket + INTERVAL '1 minute' <= %2$L
          AND NOT (bucket + INTERVAL '1 minute' > %3$L AND bucket < %4$L)
          AND extract(hour FROM bucket AT TIME ZONE %5$L) NOT BETWEEN 6 AND 21  -- 22:00–05:59
    ), nights AS (  -- complete nights only (≥ 7 of 8 hours of data)
        SELECT resident_id, night,
               state_agg(bucket, CASE WHEN motion_sum IS NULL THEN 'no_data'
                                      WHEN motion_sum < %6$s THEN 'still'
                                      ELSE 'moving' END) AS motion_states
        FROM night_minutes
        GROUP BY 1, 2
        HAVING count(*) >= 420
    ), exits AS (
        SELECT resident_id, ((ts AT TIME ZONE %5$L) - INTERVAL '12 hours')::date AS night, count(*) AS n
        FROM events
        WHERE type = 'bed_exit' AND ts >= %1$L AND ts < %2$L
          AND extract(hour FROM ts AT TIME ZONE %5$L) NOT BETWEEN 6 AND 21
        GROUP BY 1, 2
    ), per_night AS (
        SELECT n.resident_id,
               coalesce(e.n, 0)::double precision AS bed_exits,
               coalesce((SELECT max(extract(epoch FROM p.end_time - p.start_time)) / 60
                           FROM state_periods(n.motion_states, 'still') AS p), 0)::double precision AS still_minutes
        FROM nights n
        LEFT JOIN exits e USING (resident_id, night)
    ), per_resident AS (
        SELECT resident_id,
               percentile_agg(bed_exits)     AS exits_p, stats_agg(bed_exits)     AS exits_s,
               percentile_agg(still_minutes) AS still_p, stats_agg(still_minutes) AS still_s
        FROM per_night
        GROUP BY 1
    )
    SELECT r.resident_id, -1::smallint AS tod_block, v.metric,
           approx_percentile(0.05, v.pct) AS p05, approx_percentile(0.50, v.pct) AS p50,
           approx_percentile(0.95, v.pct) AS p95, average(v.st) AS mean, stddev(v.st) AS stddev,
           num_vals(v.st) AS n, %7$L::timestamptz AS updated_at
    FROM per_resident r
    CROSS JOIN LATERAL (VALUES ('night_bed_exits', r.exits_p, r.exits_s),
                               ('still_minutes', r.still_p, r.still_s)) AS v (metric, pct, st)
    $sql$, win_start, win_end, hold_start, hold_end, tz, still_motion, as_of);

    INSERT INTO resident_baselines (resident_id, tod_block, metric, p05, p50, p95, mean, stddev, n, updated_at)
    SELECT resident_id, tod_block, metric, p05, p50, p95, mean, stddev, n, updated_at FROM _baseline_new
    UNION ALL
    SELECT resident_id, tod_block, metric, p05, p50, p95, mean, stddev, n, updated_at FROM _baseline_behavior
    ON CONFLICT (resident_id, tod_block, metric) DO UPDATE
       SET p05 = excluded.p05, p50 = excluded.p50, p95 = excluded.p95, mean = excluded.mean,
           stddev = excluded.stddev, n = excluded.n, updated_at = excluded.updated_at;
    GET DIAGNOSTICS n_rows = ROW_COUNT;
    DROP TABLE pg_temp._baseline_new, pg_temp._baseline_behavior;

    RETURN n_rows;
END;
$$;

-- Scheduler entry point: the only place that supplies now() as as_of.
CREATE OR REPLACE PROCEDURE job_refresh_resident_baselines(job_id int, config jsonb)
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM refresh_resident_baselines(
        now(),
        coalesce((config ->> 'window_days')::int, 7),
        coalesce((config ->> 'exclude_recent')::interval, INTERVAL '6 hours'));
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs WHERE proc_name = 'job_refresh_resident_baselines') THEN
        PERFORM add_job('job_refresh_resident_baselines', INTERVAL '1 hour',
                        config => '{"window_days": 7, "exclude_recent": "6 hours"}',
                        job_name => 'vigil_resident_baselines');
    END IF;
END;
$$;

-- Re-enable Vigil's jobs if an interrupted `make seed` left them paused (the seed pauses them while it
-- rewrites history and normally resumes them itself). Every seed and `make migrate` runs this.
SELECT alter_job(job_id, scheduled => true)
FROM timescaledb_information.jobs
WHERE NOT scheduled
  AND (hypertable_name IN ('vitals', 'priority_snapshots', 'vitals_1m', 'vitals_15m', 'vitals_1h')
       OR proc_name = 'job_refresh_resident_baselines');
