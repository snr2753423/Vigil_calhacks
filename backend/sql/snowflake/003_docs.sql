-- 003_docs.sql: care plans → Cortex Search (SPEC §9 ④). Run by `make care-plans` (scripts/index_care_plans.py),
-- which first writes the PDFs and substitutes ${CARE_PLANS_DIR}. Idempotent: re-running re-uploads, re-parses
-- and rebuilds the derived tables and the search service.

CREATE SCHEMA IF NOT EXISTS DOCS;

-- Directory table on, server-side encryption (AI_PARSE_DOCUMENT needs SSE on internal stages).
CREATE STAGE IF NOT EXISTS DOCS.CARE_PLANS
  DIRECTORY = (ENABLE = TRUE)
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  COMMENT = 'Synthetic care-plan PDFs, one per resident (resident_NNN.pdf)';

PUT 'file://${CARE_PLANS_DIR}/resident_*.pdf' @DOCS.CARE_PLANS AUTO_COMPRESS = FALSE OVERWRITE = TRUE;

ALTER STAGE DOCS.CARE_PLANS REFRESH;

-- One row per PDF: the LAYOUT parse keeps headings as Markdown, so sections can be split out below.
CREATE OR REPLACE TABLE DOCS.CARE_PLAN_DOCS AS
SELECT
    relative_path,
    TO_NUMBER(REGEXP_SUBSTR(relative_path, 'resident_([0-9]+)', 1, 1, 'e', 1)) AS resident_id,
    AI_PARSE_DOCUMENT(TO_FILE('@DOCS.CARE_PLANS', relative_path), {'mode': 'LAYOUT'}):content::VARCHAR AS content,
    last_modified,
    SYSDATE() AS parsed_at
FROM DIRECTORY(@DOCS.CARE_PLANS)
WHERE relative_path ILIKE 'resident_%.pdf';

-- Split each document at its Markdown headings (one piece per care-plan section), then
-- SPLIT_TEXT_RECURSIVE_CHARACTER each section into ~1000-char chunks with 150 overlap. Most sections fit in one
-- chunk, so a citation's section label is always the heading it came from.
CREATE OR REPLACE TABLE DOCS.CARE_PLAN_CHUNKS AS
WITH sections AS (
    SELECT
        d.resident_id,
        d.relative_path,
        s.index AS section_idx,
        s.value::VARCHAR AS section_text
    FROM DOCS.CARE_PLAN_DOCS d,
         LATERAL FLATTEN(input => SPLIT(REGEXP_REPLACE(d.content, '\n(#+ )', '\n~~SECTION~~\\1'), '~~SECTION~~')) s
)
SELECT
    s.resident_id,
    COALESCE(
        NULLIF(TRIM(REGEXP_REPLACE(REGEXP_SUBSTR(s.section_text, '^#+ [^\n]+'), '^#+ ', '')), ''),
        'Resident'
    ) AS section,
    s.section_idx,
    c.index AS chunk_idx,
    TRIM(c.value::VARCHAR) AS chunk,
    s.relative_path
FROM sections s,
     LATERAL FLATTEN(input => SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(s.section_text, 'markdown', 1000, 150)) c
WHERE LENGTH(TRIM(c.value::VARCHAR)) > 0;

-- Hybrid (vector + keyword) search over the chunks; filter by resident_id at query time (REST :query).
CREATE OR REPLACE CORTEX SEARCH SERVICE DOCS.CARE_PLAN_SEARCH
  ON chunk
  ATTRIBUTES resident_id, section
  WAREHOUSE = VIGIL_WH
  TARGET_LAG = '1 day'
  COMMENT = 'Vigil care-plan chunks (SPEC §9 ④)'
  AS (
    SELECT chunk, resident_id, section, section_idx, chunk_idx, relative_path
    FROM DOCS.CARE_PLAN_CHUNKS
  );
