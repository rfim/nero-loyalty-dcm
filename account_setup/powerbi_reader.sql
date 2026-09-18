-- =============================================================================
-- Power BI read-only service identity, scoped to exactly the marts needed
-- for the Part 4 data-visualisation deliverable:
--   - mart_store_daily_performance: store/region loyalty engagement,
--     best/worst, recent change
--   - mart_customer_loyalty_activity: does tier show up in basket size or
--     visit frequency (PII-safe as of this file -- see the model itself:
--     exposes customer_key, never the raw customer_id)
--   - mart_reward_redemption_daily: redemption patterns by store/reward
--
-- Deliberately its own role/user rather than reusing NERO_BI_ROLE (which
-- Streamlit apps already use) -- same one-identity-per-workload pattern as
-- every other service user in this repo, so a Power BI credential leak
-- can't be used against anything else, and Power BI's access can be
-- revoked without touching any Streamlit app.
--
-- Scoped to the marts only, not the underlying NERO_ANALYTICS."02_GOLD"
-- dims/facts -- the marts already carry everything both required questions
-- need (region, tier, sales, visit counts), so there's no reason to widen
-- the surface Power BI can see.
--
-- Apply with: snow sql -f account_setup/powerbi_reader.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS NERO_POWERBI_ROLE
  COMMENT = 'Read-only reporting role for the external Power BI connection. SELECT on the marketing/operations marts only -- see account_setup/powerbi_reader.sql.';

CREATE USER IF NOT EXISTS NERO_POWERBI_USER
  TYPE = SERVICE
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsKJs6Ut1KxFinOo6f7Jz7s2HH71wX2uPqWxWtfEAoM0Lj2KnACVYu2NFnhz64D5+vPDntihas6Mo48aoyMQPjA9Y13Jg0LJHxkXUPJ2mv5phi6icLzU2Le0uJHsW3QqIToPHAay/aNszgeMQ+C36nUA519sAtstgBA8n5ASGRUUmnX22fTCRHi2MqxfnM96xS7s11Z75WNHZo0SROO/GkkeEFgamJo2L2x8GLJiLKnqkrRe01I/vR0Fs2gmWUKI2M8tX/SAj59EUZv7Zi8LmlYC4DynJJkD7OtHE/yotw7BdC3XoI7s8PnKLvuWbI7JmY5CJkwWMxXnaLJUDoJrzbQIDAQAB'
  DEFAULT_ROLE = NERO_POWERBI_ROLE
  DEFAULT_WAREHOUSE = NERO_BI_WH
  COMMENT = 'Service identity for the external Power BI connection (Part 4 dashboard). Key-pair auth only, no password.';

GRANT ROLE NERO_POWERBI_ROLE TO USER NERO_POWERBI_USER;

GRANT USAGE ON WAREHOUSE NERO_BI_WH TO ROLE NERO_POWERBI_ROLE;

GRANT USAGE ON DATABASE NERO_ANALYTICS TO ROLE NERO_POWERBI_ROLE;
GRANT USAGE ON SCHEMA NERO_ANALYTICS."03_MART_MARKETING" TO ROLE NERO_POWERBI_ROLE;
GRANT USAGE ON SCHEMA NERO_ANALYTICS."04_MART_OPERATIONS" TO ROLE NERO_POWERBI_ROLE;

GRANT SELECT ON ALL VIEWS IN SCHEMA NERO_ANALYTICS."03_MART_MARKETING" TO ROLE NERO_POWERBI_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA NERO_ANALYTICS."03_MART_MARKETING" TO ROLE NERO_POWERBI_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA NERO_ANALYTICS."04_MART_OPERATIONS" TO ROLE NERO_POWERBI_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA NERO_ANALYTICS."04_MART_OPERATIONS" TO ROLE NERO_POWERBI_ROLE;
