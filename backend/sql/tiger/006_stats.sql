-- 006_stats.sql: cumulative Snowflake API call counters (SPEC §13 proof panel). Idempotent.

-- Every process that talks to Snowflake (backend, make train/detect/prepare/seed/care-plans) adds its call
-- counts here, so /under-the-hood shows totals across processes and restarts, not just this backend's.
CREATE TABLE IF NOT EXISTS snowflake_calls (
    feature  text             PRIMARY KEY,  -- e.g. rest.cortex_search, anomaly.detect.hr, sync.vitals_15m
    calls    bigint           NOT NULL DEFAULT 0,
    errors   bigint           NOT NULL DEFAULT 0,
    total_ms double precision NOT NULL DEFAULT 0,
    first_at timestamptz      NOT NULL DEFAULT now(),
    last_at  timestamptz      NOT NULL DEFAULT now()
);
