-- =============================================================================
-- Fix stale MAIN_FILE on the 10 leader persona apps after the
-- one-app-one-subfolder restructure (leaders/<name>.py -> leaders/<name>/
-- <name>.py).
--
-- `snow streamlit deploy --replace` on an *already-existing* STREAMLIT
-- object only uploads/overwrites stage files (and removes pruned ones with
-- --prune) -- it does NOT sync entity-level properties like MAIN_FILE from
-- snowflake.yml on an update. That only happens on the initial CREATE. So
-- when the leaders/ restructure changed each entity's main_file path in
-- streamlit_apps/snowflake.yml and redeployed (the apps already existed
-- from the earlier streamlit_apps schema reorg), the stage content updated
-- correctly but each app's MAIN_FILE property stayed pointed at the old
-- flat path -- which --prune had just deleted from the stage, so every
-- leader app broke with "The file specified in the MAIN_FILE property...
-- could not be found."
--
-- Lesson for next time: after any change to an entity's `main_file` (or
-- other top-level property) in snowflake.yml for an app that already
-- exists in Snowflake, `snow streamlit deploy` is not enough on its own --
-- follow it with an explicit ALTER STREAMLIT ... SET MAIN_FILE = '...'
-- (or --force to fully recreate the object, which loses run history).
--
-- Idempotent: safe to re-run (each ALTER just re-asserts the same value).
-- Must be run per-app via that app's owning-role connection --
-- ACCOUNTADMIN has no MODIFY privilege on objects it doesn't own. Apply
-- with, e.g.:
--   snow sql -f account_setup/leaders_main_file_fix.sql -c nero_marketing_test
-- (repeat once per connection listed below; a single ACCOUNTADMIN-run
-- pass will fail with "Insufficient privileges ... must have MODIFY").
-- =============================================================================

-- nero_marketing_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.MARKETING_ASSISTANT SET MAIN_FILE = 'leaders/marketing_assistant/marketing_assistant.py';
-- nero_ops_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.OPS_ASSISTANT SET MAIN_FILE = 'leaders/ops_assistant/ops_assistant.py';
-- nero_finance_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.FINANCE_ASSISTANT SET MAIN_FILE = 'leaders/finance_assistant/finance_assistant.py';
-- nero_security_lead_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.SECURITY_LEAD_ASSISTANT SET MAIN_FILE = 'leaders/security_lead_assistant/security_lead_assistant.py';
-- nero_platform_lead_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.PLATFORM_LEAD_ASSISTANT SET MAIN_FILE = 'leaders/platform_lead_assistant/platform_lead_assistant.py';
-- nero_ceo_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.CEO_ASSISTANT SET MAIN_FILE = 'leaders/ceo_assistant/ceo_assistant.py';
-- nero_cx_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.CX_ASSISTANT SET MAIN_FILE = 'leaders/cx_assistant/cx_assistant.py';
-- nero_cdo_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.CDO_ASSISTANT SET MAIN_FILE = 'leaders/cdo_assistant/cdo_assistant.py';
-- nero_audit_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.AUDIT_ASSISTANT SET MAIN_FILE = 'leaders/audit_assistant/audit_assistant.py';
-- nero_regional_test:
ALTER STREAMLIT NERO_GOVERNANCE.APPS_LEADERS.REGIONAL_ASSISTANT SET MAIN_FILE = 'leaders/regional_assistant/regional_assistant.py';
