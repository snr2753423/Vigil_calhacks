-- 002_tables.sql: VIGIL.CORE tables (SPEC §9). Idempotent; `make migrate-sf` applies it as VIGIL_ROLE.
-- Every timestamp is TIMESTAMP_NTZ holding UTC (the ML functions want NTZ; Tiger stores timestamptz UTC).

CREATE SCHEMA IF NOT EXISTS CORE;
CREATE SCHEMA IF NOT EXISTS ML;
CREATE SCHEMA IF NOT EXISTS DOCS;

-- --------------------------------------------------------------------------- reference (full copy per seed)

CREATE TABLE IF NOT EXISTS CORE.RESIDENTS (
    RESIDENT_ID       NUMBER(5)     NOT NULL PRIMARY KEY,
    NAME              VARCHAR       NOT NULL,
    AGE               NUMBER(3),
    SEX               VARCHAR(1),
    ROOM              VARCHAR(8),
    UNIT              VARCHAR(16),
    DIAGNOSES         ARRAY,
    MEDICATIONS       VARIANT,
    FLAGS             ARRAY,
    MORSE             NUMBER(3),
    MOBILITY          VARCHAR(16),
    COGNITION         VARCHAR(16),
    CODE_STATUS       VARCHAR(16),
    EMERGENCY_CONTACT VARIANT,
    PROFILE           VARIANT,
    IS_LIVE           BOOLEAN,
    CARE_PLAN_URI     VARCHAR,
    SYNCED_AT         TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.STAFF (
    STAFF_ID  NUMBER(5) NOT NULL PRIMARY KEY,
    NAME      VARCHAR   NOT NULL,
    ROLE      VARCHAR(8),
    SHIFT     VARCHAR(16),
    UNIT      VARCHAR(16),
    SYNCED_AT TIMESTAMP_NTZ
);

-- --------------------------------------------------------------------------- time series (synced every minute)

-- Closed 15-minute buckets from Tiger's vitals_15m continuous aggregate; same column names, upper-cased.
CREATE TABLE IF NOT EXISTS CORE.VITALS_15M (
    RESIDENT_ID    NUMBER(5)     NOT NULL,
    BUCKET_TS      TIMESTAMP_NTZ NOT NULL,  -- bucket start, UTC
    HR_AVG         FLOAT,
    HR_MIN         FLOAT,
    HR_MAX         FLOAT,
    HR_P50         FLOAT,                   -- approx_percentile(0.5, hr_pct) of the 1-min HR
    HRV_AVG        FLOAT,
    RR_AVG         FLOAT,
    RR_MIN         FLOAT,
    RR_MAX         FLOAT,
    SPO2_MIN       FLOAT,
    SKIN_TEMP_AVG  FLOAT,
    MOTION_SUM     FLOAT,
    IMPACT_MAX     FLOAT,
    IN_BED_FRAC    FLOAT,
    BED_MOTION_AVG FLOAT,
    WORN_FRAC      FLOAT,
    N              NUMBER(10),
    SYNCED_AT      TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.EVENTS (
    TS          TIMESTAMP_NTZ NOT NULL,
    RESIDENT_ID NUMBER(5)     NOT NULL,
    TYPE        VARCHAR(32)   NOT NULL,
    STAFF_ID    NUMBER(5),
    PAYLOAD     VARIANT,
    SYNCED_AT   TIMESTAMP_NTZ
);

-- Typed views of the event stream, written by the same sync (Cortex Analyst and AI_AGG read these).
CREATE TABLE IF NOT EXISTS CORE.CHECKS (
    TS          TIMESTAMP_NTZ NOT NULL,
    RESIDENT_ID NUMBER(5)     NOT NULL,
    STAFF_ID    NUMBER(5),
    KIND        VARCHAR(40),
    REASON      VARCHAR,
    SOURCE      VARCHAR(16),
    SYNCED_AT   TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.NURSE_NOTES (
    TS          TIMESTAMP_NTZ NOT NULL,
    RESIDENT_ID NUMBER(5)     NOT NULL,
    STAFF_ID    NUMBER(5),
    AUTHOR_ROLE VARCHAR(8),
    SOURCE      VARCHAR(16),           -- text | voice
    ROUTINE     BOOLEAN,
    TEXT        VARCHAR,
    SYNCED_AT   TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.ALERTS (
    TS          TIMESTAMP_NTZ NOT NULL,
    RESIDENT_ID NUMBER(5)     NOT NULL,
    EVENT       VARCHAR(24)   NOT NULL,  -- raised | acknowledged | resolved
    TIER        VARCHAR(10),
    FROM_TIER   VARCHAR(10),
    SCORE       FLOAT,
    RULE        VARCHAR(32),
    FACTORS     VARIANT,
    REASON      VARCHAR(32),
    SYNCED_AT   TIMESTAMP_NTZ
);

-- --------------------------------------------------------------------------- deep-tier outputs

-- SNOWFLAKE.ML.ANOMALY_DETECTION per resident × metric × closed bucket (SPEC §9 ②). SEVERITY is Vigil's
-- 0..1 mapping of PERCENTILE (app/deep/anomaly.py); it is also mirrored to Tiger ai_insights.
CREATE TABLE IF NOT EXISTS CORE.ANOMALY_RESULTS (
    RESIDENT_ID NUMBER(5)     NOT NULL,
    METRIC      VARCHAR(16)   NOT NULL,  -- hr | rr | spo2 | skin_temp | motion
    BUCKET_TS   TIMESTAMP_NTZ NOT NULL,
    Y           FLOAT,
    FORECAST    FLOAT,
    LOWER_BOUND FLOAT,
    UPPER_BOUND FLOAT,
    IS_ANOMALY  BOOLEAN,
    PERCENTILE  FLOAT,
    DISTANCE    FLOAT,
    SEVERITY    FLOAT,
    MODEL       VARCHAR(32),
    DETECTED_AT TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.FORECAST_RESULTS (
    RESIDENT_ID NUMBER(5)     NOT NULL,
    METRIC      VARCHAR(16)   NOT NULL,
    AS_OF       TIMESTAMP_NTZ NOT NULL,  -- when the forecast was made (its training data ends here)
    TS          TIMESTAMP_NTZ NOT NULL,  -- forecast bucket
    FORECAST    FLOAT,
    LOWER_BOUND FLOAT,
    UPPER_BOUND FLOAT,
    CREATED_AT  TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.AI_ASSESSMENTS (
    RESIDENT_ID NUMBER(5)     NOT NULL,
    AS_OF       TIMESTAMP_NTZ NOT NULL,
    MODEL       VARCHAR(64),
    TRIGGERED_BY VARCHAR(32),
    PAYLOAD     VARIANT,
    FALLBACK    BOOLEAN,
    LATENCY_MS  NUMBER(10),
    CREATED_AT  TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS CORE.HANDOFFS (
    SHIFT_DATE  DATE          NOT NULL,
    SHIFT       VARCHAR(16)   NOT NULL,
    MODEL       VARCHAR(64),
    PAYLOAD     VARIANT,
    CREATED_AT  TIMESTAMP_NTZ
);
