-- Unity Catalog setup for the Data Governance Business Glossary app.
-- These app-owned tables live in the WORKFLOW catalog/schema from config.json
-- (independent of the catalogs the app governs). Fill the placeholders and run once.
--   {{WF_CATALOG}}  workflow_catalog, e.g. main
--   {{WF_SCHEMA}}   workflow_schema,  e.g. data_governance_app
--   {{APP_SP}}      the app's service principal id (Compute > App > Authorization)
-- Backtick-quote any identifier containing a hyphen, e.g. `my-catalog`.

CREATE SCHEMA IF NOT EXISTS {{WF_CATALOG}}.{{WF_SCHEMA}};

-- 1. Proposals table (two-step approval workflow + audit trail).
CREATE TABLE IF NOT EXISTS {{WF_CATALOG}}.{{WF_SCHEMA}}.glossary_proposals (
  id                STRING,
  catalog_name      STRING,
  schema_name       STRING,
  table_name        STRING,
  column_name       STRING,
  current_comment   STRING,
  proposed_comment  STRING,
  proposed_tags     STRING,  -- JSON: {"sensitivity","synonyms","reason"}
  proposer          STRING,
  status            STRING,  -- pending | step1_approved | approved | returned
  reviewer          STRING,
  review_note       STRING,
  created_at        STRING,
  reviewed_at       STRING,
  step1_reviewer    STRING,
  step1_reviewed_at STRING
);

-- 2. Stewardship model (data domain -> owner / steward; used for role resolution).
CREATE TABLE IF NOT EXISTS {{WF_CATALOG}}.{{WF_SCHEMA}}.data_stewards (
  id                 BIGINT GENERATED ALWAYS AS IDENTITY,
  data_domain        STRING NOT NULL,
  data_steward_name  STRING,
  data_steward_email STRING,
  data_owner_name    STRING,
  data_owner_email   STRING,
  is_active          BOOLEAN,
  created_at         TIMESTAMP,
  updated_at         TIMESTAMP
);
-- (is_active/created_at/updated_at are set by the app; no column DEFAULTs so the
--  table works without the delta.feature.allowColumnDefaults table property.)

-- 3. Grants for the app service principal on the WORKFLOW schema (its own state).
GRANT USE CATALOG ON CATALOG {{WF_CATALOG}} TO `{{APP_SP}}`;
GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA {{WF_CATALOG}}.{{WF_SCHEMA}} TO `{{APP_SP}}`;

-- 4. Grants on each catalog the app should GOVERN. SELECT lets it read definitions;
--    MODIFY + APPLY TAG let an approval write COMMENT + sensitivity TAG back.
--    Repeat per governed catalog (or scope to specific schemas).
-- GRANT USE CATALOG ON CATALOG <governed_catalog> TO `{{APP_SP}}`;
-- GRANT USE SCHEMA, SELECT, MODIFY, APPLY TAG ON CATALOG <governed_catalog> TO `{{APP_SP}}`;
