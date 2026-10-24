-- 005_stages.sql: Phase 9 objects (SPEC §9 ③ ⑥ ⑦ ⑧). Idempotent; `make migrate-sf` applies it as VIGIL_ROLE.

-- Voice notes recorded in the browser (webm / m4a), PUT by the backend, read by AI_TRANSCRIBE.
CREATE STAGE IF NOT EXISTS CORE.NOTE_AUDIO
  DIRECTORY = (ENABLE = TRUE)
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  COMMENT = 'Nurse voice notes (synthetic demo), one file per note';

-- The Cortex Analyst semantic model (005_semantic.yaml → @CORE.SEMANTIC/vigil.yaml).
CREATE STAGE IF NOT EXISTS CORE.SEMANTIC
  DIRECTORY = (ENABLE = TRUE)
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  COMMENT = 'Cortex Analyst semantic model for Ask the facility';

-- AI_EXTRACT output per nurse note (text or transcribed voice). NOTE_TS joins CORE.NURSE_NOTES.TS.
CREATE TABLE IF NOT EXISTS CORE.NOTE_EXTRACTS (
    RESIDENT_ID      NUMBER(5)     NOT NULL,
    NOTE_TS          TIMESTAMP_NTZ NOT NULL,
    SOURCE           VARCHAR(16),            -- text | voice
    TEXT             VARCHAR,
    CONFUSION_CHANGE VARCHAR(16),            -- new | worse | same | better | unknown
    ORAL_INTAKE      VARCHAR(16),            -- normal | reduced | refused | unknown
    CONCERN_LEVEL    VARCHAR(8),             -- none | low | med | high
    PAYLOAD          VARIANT,                -- every extracted field + the raw AI_EXTRACT response
    EXTRACTED_AT     TIMESTAMP_NTZ
);
