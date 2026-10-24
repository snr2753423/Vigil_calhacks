-- 001_schema.sql: Vigil core tables on Tiger Cloud (SPEC §6). Idempotent; `make migrate` re-runs it safely.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

-- --------------------------------------------------------------------------- reference tables

CREATE TABLE IF NOT EXISTS residents (
    resident_id       int PRIMARY KEY,
    name              text        NOT NULL,
    age               int         NOT NULL,
    sex               text        NOT NULL CHECK (sex IN ('F', 'M')),
    room              text        NOT NULL,
    unit              text        NOT NULL CHECK (unit IN ('East', 'West', 'Memory Care')),
    diagnoses         text[]      NOT NULL DEFAULT '{}',
    medications       jsonb       NOT NULL DEFAULT '[]',   -- [{name, dose, flags: [...]}]
    flags             text[]      NOT NULL DEFAULT '{}',   -- beta_blocker | afib | copd | insulin | opioid
    morse             int         NOT NULL CHECK (morse BETWEEN 0 AND 125),
    mobility          text        NOT NULL CHECK (mobility IN ('independent', '1-assist', '2-assist', 'bedbound')),
    cognition         text        NOT NULL CHECK (cognition IN ('intact', 'mild', 'moderate', 'severe')),
    code_status       text        NOT NULL CHECK (code_status IN ('FULL', 'DNR', 'DNR-DNI', 'COMFORT')),
    emergency_contact jsonb,
    profile           jsonb       NOT NULL DEFAULT '{}',   -- care-plan facts: behaviors, escalation prefs, ...
    is_live           boolean     NOT NULL DEFAULT false,  -- true only for R-120 (presenter, real devices)
    care_plan_uri     text                                 -- set when the care plan PDF is staged (Phase 6)
);

CREATE TABLE IF NOT EXISTS staff (
    staff_id int PRIMARY KEY,
    name     text NOT NULL,
    role     text NOT NULL CHECK (role IN ('RN', 'LPN', 'CNA')),
    shift    text NOT NULL CHECK (shift IN ('day', 'evening', 'night')),
    unit     text CHECK (unit IN ('East', 'West', 'Memory Care'))  -- CNAs only
);

CREATE TABLE IF NOT EXISTS devices (
    device_id   text PRIMARY KEY,
    resident_id int REFERENCES residents (resident_id) ON DELETE SET NULL,
    kind        text     NOT NULL CHECK (kind IN ('wristband', 'bed_sensor')),
    model       text     NOT NULL,
    source      smallint NOT NULL DEFAULT 0,  -- 0 = sim, 1 = polar, 2 = phone (same codes as vitals.source)
    active      boolean  NOT NULL DEFAULT true,
    assigned_at timestamptz
);

-- --------------------------------------------------------------------------- vitals (hypertable)
-- One wide row per resident per tick. Metrics a device did not report on that tick are NULL.

CREATE TABLE IF NOT EXISTS vitals (
    ts          timestamptz NOT NULL,
    resident_id int         NOT NULL,
    hr          real,        -- bpm, wristband (5 s)
    hrv_rmssd   real,        -- ms, wristband (5 min)
    skin_temp   real,        -- °C at the wrist (1 min); a change-vs-baseline feature, never NEWS2
    spo2        real,        -- %, wristband (5 min)
    motion      real,        -- wrist accelerometer activity counts per 5 s
    impact      real,        -- peak g, only on ticks with an impact
    worn        boolean,     -- wristband skin contact
    rr          real,        -- breaths/min, bed sensor (only while in bed)
    in_bed      boolean,     -- bed sensor presence
    bed_motion  real,        -- bed sensor movement level
    source      smallint    NOT NULL DEFAULT 0  -- 0 = sim, 1 = polar, 2 = phone
);

SELECT create_hypertable('vitals', by_range('ts', INTERVAL '1 day'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS vitals_resident_ts_idx ON vitals (resident_id, ts DESC);
ALTER TABLE vitals SET (
    timescaledb.enable_columnstore = true,
    timescaledb.segmentby = 'resident_id',
    timescaledb.orderby = 'ts DESC'
);

-- --------------------------------------------------------------------------- events (hypertable)

CREATE TABLE IF NOT EXISTS events (
    ts          timestamptz NOT NULL,
    resident_id int         NOT NULL,
    type        text        NOT NULL CHECK (type IN (
        'bed_exit', 'bed_return', 'fall_suspected', 'check_logged', 'note_added', 'intake_logged',
        'spot_check', 'device_offline', 'device_online', 'alert_raised', 'alert_acknowledged', 'alert_resolved'
    )),
    staff_id    int,
    payload     jsonb       NOT NULL DEFAULT '{}'
);

SELECT create_hypertable('events', by_range('ts', INTERVAL '7 days'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS events_resident_ts_idx ON events (resident_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_type_ts_idx ON events (type, ts DESC);

-- --------------------------------------------------------------------------- priority_snapshots (hypertable)

CREATE TABLE IF NOT EXISTS priority_snapshots (
    ts          timestamptz NOT NULL,
    resident_id int         NOT NULL,
    score       real        NOT NULL,
    tier        text        NOT NULL CHECK (tier IN ('CRITICAL', 'HIGH', 'WATCH', 'STABLE')),
    components  jsonb       NOT NULL DEFAULT '{}',
    factors     jsonb       NOT NULL DEFAULT '[]'
);

SELECT create_hypertable('priority_snapshots', by_range('ts', INTERVAL '1 day'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS priority_snapshots_resident_ts_idx ON priority_snapshots (resident_id, ts DESC);
ALTER TABLE priority_snapshots SET (
    timescaledb.enable_columnstore = true,
    timescaledb.segmentby = 'resident_id',
    timescaledb.orderby = 'ts DESC'
);

-- --------------------------------------------------------------------------- baselines, AI cache, sync

-- Personal "normal" per resident. tod_block 0–5 = 4-hour blocks of facility local time (0 = 00:00–04:00).
-- tod_block -1 holds per-night behavior metrics: 'night_bed_exits' (count 22:00–06:00) and
-- 'still_minutes' (longest still period per night); p05..stddev are over nights.
CREATE TABLE IF NOT EXISTS resident_baselines (
    resident_id int         NOT NULL,
    tod_block   smallint    NOT NULL CHECK (tod_block BETWEEN -1 AND 5),
    metric      text        NOT NULL,
    p05         real,
    p50         real,
    p95         real,
    mean        real,
    stddev      real,
    n           bigint      NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (resident_id, tod_block, metric)
);

-- One row per resident with the behavior baselines the fast tier reads (SPEC §6 names).
CREATE OR REPLACE VIEW resident_behavior_baselines AS
SELECT resident_id,
       max(p95) FILTER (WHERE metric = 'night_bed_exits') AS night_bed_exits_p95,
       max(p95) FILTER (WHERE metric = 'still_minutes')   AS still_minutes_p95,
       max(updated_at)                                    AS updated_at
FROM resident_baselines
WHERE tod_block = -1
GROUP BY resident_id;

-- Cache of Snowflake outputs, mirrored back so the UI never waits on Snowflake.
CREATE TABLE IF NOT EXISTS ai_insights (
    resident_id int         NOT NULL,
    kind        text        NOT NULL CHECK (kind IN ('anomaly', 'forecast', 'assessment', 'note_extract')),
    as_of       timestamptz NOT NULL,
    payload     jsonb       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (resident_id, kind, as_of)
);

-- Watermarks for the Tiger → Snowflake sync (Phase 5).
CREATE TABLE IF NOT EXISTS sync_state (
    name        text PRIMARY KEY,
    watermark   timestamptz,
    rows_synced bigint      NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Small key/value store for demo metadata written by the seed: facility tz, the scripted
-- "last night" window, and the baseline holdout (last night is excluded from baselines so
-- replay is out-of-sample, the same rule the Snowflake models follow).
CREATE TABLE IF NOT EXISTS sim_meta (
    key        text PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
