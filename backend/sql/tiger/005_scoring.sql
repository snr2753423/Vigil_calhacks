-- 005_scoring.sql: indexes for the fast-tier evaluator (SPEC §7a). Idempotent.

-- Impact spikes are rare (a few per resident per day), so the suspected-fall lookup ("newest impact
-- in the last 2 h") reads this small partial index instead of scanning 2 h of vitals for every resident.
CREATE INDEX IF NOT EXISTS vitals_impact_idx ON vitals (ts DESC) WHERE impact IS NOT NULL;
