-- Vigil Snowflake bootstrap. Run once in Snowsight as ACCOUNTADMIN, logged in as the
-- user Vigil connects with (SNOWFLAKE_USER). Idempotent: safe to re-run.

USE ROLE ACCOUNTADMIN;
-- Quoted so IDENTIFIER() keeps the exact name; trial user names can be lowercase and case-sensitive.
SET vigil_user = '"' || CURRENT_USER() || '"';

-- App role: owns the VIGIL database, uses the warehouse, may call Cortex.
CREATE ROLE IF NOT EXISTS VIGIL_ROLE;
GRANT ROLE VIGIL_ROLE TO ROLE SYSADMIN;
GRANT ROLE VIGIL_ROLE TO USER IDENTIFIER($vigil_user);
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE VIGIL_ROLE;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE VIGIL_ROLE;

CREATE WAREHOUSE IF NOT EXISTS VIGIL_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
GRANT USAGE, OPERATE, MONITOR ON WAREHOUSE VIGIL_WH TO ROLE VIGIL_ROLE;

-- Bigger warehouse used only by `make train` (SNOWFLAKE_TRAIN_WAREHOUSE): multi-series ANOMALY_DETECTION
-- training is slow on XSMALL. Suspends after 60 s, so it costs only while a model trains.
CREATE WAREHOUSE IF NOT EXISTS VIGIL_ML_WH
  WAREHOUSE_SIZE = 'MEDIUM'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
GRANT USAGE, OPERATE, MONITOR ON WAREHOUSE VIGIL_ML_WH TO ROLE VIGIL_ROLE;

-- Claude models are not hosted natively in AWS US West 2; allow cross-region inference (SPEC §9).
ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

-- REST calls (Phase 6+) run as the user's default role.
ALTER USER IDENTIFIER($vigil_user) SET DEFAULT_ROLE = VIGIL_ROLE DEFAULT_WAREHOUSE = VIGIL_WH;

-- Create the database as VIGIL_ROLE so it owns every schema and object inside.
USE ROLE VIGIL_ROLE;
CREATE DATABASE IF NOT EXISTS VIGIL;
CREATE SCHEMA IF NOT EXISTS VIGIL.CORE;
CREATE SCHEMA IF NOT EXISTS VIGIL.ML;
CREATE SCHEMA IF NOT EXISTS VIGIL.DOCS;
