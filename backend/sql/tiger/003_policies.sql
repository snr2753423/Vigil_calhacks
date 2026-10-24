-- 003_policies.sql: refresh, columnstore (compression) and retention policies (SPEC §6). Idempotent.

-- Continuous-aggregate refresh. Windows cover a few hours of late data; longer backfills
-- (seed, live-loop catch-up) refresh their own range explicitly.
SELECT add_continuous_aggregate_policy('vitals_1m',
    start_offset => INTERVAL '3 hours', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute', if_not_exists => true);
SELECT add_continuous_aggregate_policy('vitals_15m',
    start_offset => INTERVAL '6 hours', end_offset => INTERVAL '2 minutes',
    schedule_interval => INTERVAL '1 minute', if_not_exists => true);
SELECT add_continuous_aggregate_policy('vitals_1h',
    start_offset => INTERVAL '1 day', end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '15 minutes', if_not_exists => true);

-- Columnstore: raw rows older than 1 day are compressed (segmentby resident_id, orderby ts DESC).
CALL add_columnstore_policy('vitals', after => INTERVAL '1 day', if_not_exists => true);
CALL add_columnstore_policy('priority_snapshots', after => INTERVAL '1 day', if_not_exists => true);

-- Retention: raw 90 days, aggregates 2 years.
SELECT add_retention_policy('vitals', drop_after => INTERVAL '90 days', if_not_exists => true);
SELECT add_retention_policy('priority_snapshots', drop_after => INTERVAL '90 days', if_not_exists => true);
SELECT add_retention_policy('vitals_1m', drop_after => INTERVAL '2 years', if_not_exists => true);
SELECT add_retention_policy('vitals_15m', drop_after => INTERVAL '2 years', if_not_exists => true);
SELECT add_retention_policy('vitals_1h', drop_after => INTERVAL '2 years', if_not_exists => true);
