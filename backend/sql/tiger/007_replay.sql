-- 007_replay.sql: the replay prepare pass's per-step scores (SPEC §4). Idempotent.

-- One row per resident per replay step (every 2 min of last night), written by `prepare` with upserts so a
-- re-run replaces rows in place and nothing is deleted outside scripts/reset_demo.py (AGENTS.md rule 8).
-- A replay seek seeds its alert gate from here; live scores stay in priority_snapshots.
CREATE TABLE IF NOT EXISTS replay_snapshots (
    ts          timestamptz NOT NULL,
    resident_id int         NOT NULL,
    score       real        NOT NULL,
    tier        text        NOT NULL CHECK (tier IN ('CRITICAL', 'HIGH', 'WATCH', 'STABLE')),
    factors     jsonb       NOT NULL DEFAULT '[]',
    PRIMARY KEY (resident_id, ts)
);
