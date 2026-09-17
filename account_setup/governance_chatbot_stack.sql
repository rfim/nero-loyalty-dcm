-- =============================================================================
-- Role-scoped governance chatbot: a second Nero Assistant instance for the
-- platform-engineer/admin persona, separate from chatbot_app/'s
-- business-facing one.
--
-- Streamlit apps in Snowflake always execute with the OWNER's privileges --
-- there is no per-viewer "execute as caller" mode (confirmed empirically:
-- `ALTER STREAMLIT ... SET EXECUTE_AS = CALLER` is not a supported
-- property). A genuinely role-based chatbot therefore can't be one app
-- that adapts per viewer -- it has to be separate app instances, each
-- owned by a different least-privilege role, each scoped to what that
-- role should see:
--   - NERO_BI_ROLE / chatbot_app/nero_assistant.py: loyalty/sales data
--     (Cortex Analyst) + security findings (Cortex Search).
--   - NERO_GOVERNANCE_ROLE / governance_chatbot_app/ (this one): warehouse
--     cost/budget (Cortex Analyst over a new semantic view) + the same
--     security findings search, for platform engineers.
--
-- Apply with a Python session, not `snow sql -f` (same CLI templating
-- limitation noted in cortex_chatbot_stack.sql -- this file has the same
-- dollar-quoted-adjacent-to-multi-statement shape that trips it):
--   python3 -c "
--   from snowflake.connector import connect
--   conn = connect(...)
--   for cur in conn.execute_string(open('account_setup/governance_chatbot_stack.sql').read()):
--       print(cur.query.splitlines()[0])
--   "
-- =============================================================================

-- ============================ SEMANTIC VIEW ==================================
-- Cost semantic model for Cortex Analyst -- warehouse credit usage against
-- budget. WAREHOUSE_BUDGET is a real table (governance_db.sql); WAREHOUSE_
-- CREDITS_DAILY is a view over ACCOUNT_USAGE, used successfully as a
-- semantic-view TABLES source (views work fine here, not just base tables).

CREATE OR REPLACE SEMANTIC VIEW NERO_GOVERNANCE.CORTEX_TOOLS.GOVERNANCE_SEMANTIC_VIEW
  TABLES (
    WAREHOUSE_BUDGET AS NERO_GOVERNANCE.COST.WAREHOUSE_BUDGET PRIMARY KEY (WAREHOUSE_NAME)
      COMMENT = 'Monthly credit quota per NERO_*_WH warehouse.',
    WAREHOUSE_CREDITS AS NERO_GOVERNANCE.COST.WAREHOUSE_CREDITS_DAILY PRIMARY KEY (WAREHOUSE_NAME, USAGE_DATE)
      COMMENT = 'Daily credit consumption per warehouse, from ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY.'
  )
  RELATIONSHIPS (
    CREDITS_TO_BUDGET AS WAREHOUSE_CREDITS(WAREHOUSE_NAME) REFERENCES WAREHOUSE_BUDGET(WAREHOUSE_NAME)
  )
  FACTS (
    WAREHOUSE_CREDITS.CREDITS_USED AS CREDITS_USED COMMENT = 'Credits used that day.'
  )
  DIMENSIONS (
    WAREHOUSE_BUDGET.WORKLOAD AS WORKLOAD,
    WAREHOUSE_BUDGET.CREDIT_QUOTA AS CREDIT_QUOTA,
    WAREHOUSE_CREDITS.USAGE_DATE AS USAGE_DATE
  )
  METRICS (
    WAREHOUSE_CREDITS.TOTAL_CREDITS AS SUM(WAREHOUSE_CREDITS.CREDITS_USED) COMMENT = 'Total credits consumed.'
  )
  COMMENT = 'Nero platform cost semantic model for Cortex Analyst -- warehouse credit usage against budget, for governance/admin questions.';

-- ================================ ROLE / USER =================================

CREATE ROLE IF NOT EXISTS NERO_GOVERNANCE_ROLE
  COMMENT = 'Platform-engineer / admin persona: read access to cost and security governance data (budgets, credits, findings, grants, MCP audit log) -- broader than NERO_BI_ROLE''s general business-reporting scope, still read-only everywhere.';

CREATE USER IF NOT EXISTS NERO_GOVERNANCE_USER
  TYPE = SERVICE
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAx5KOnolS8NinTNQq8yMwQYJAXzYH2XxVQp9+rKpzhAW3krB2WHkfRAfwWkafyx8dmsOivTIb9lXqWMKPpCbZZC7YGLvg3U+VxF/LKAh3b8Cv+6DBoSllKZmuC1e4vlyltExT8BoxED6w3rekXVjN3rT/60/hzQwe5a+K7wvwqO9NjF6uf66+5mWMrtOVovUrXrp7XAwMsibhtCoM6pvDic15HeelIZ4WZzS+HOAzS7n8EmF1wRaa0IvC5ENRgoqA05Zkh13iRT8iiH5AwLfnEUJZVokoCSrN1/qYc+BqS6lH53VaoSA2XgNmhNJoje6PwtQVuKv+XHXxjLIcm+WzMwIDAQAB'
  DEFAULT_ROLE = NERO_GOVERNANCE_ROLE
  DEFAULT_WAREHOUSE = NERO_BI_WH
  COMMENT = 'Service identity owning the Nero Governance Assistant chatbot (governance_chatbot_app/) -- the platform-engineer-facing counterpart to NERO_BI_USER''s business-facing Nero Assistant.';

GRANT ROLE NERO_GOVERNANCE_ROLE TO USER NERO_GOVERNANCE_USER;

GRANT USAGE ON WAREHOUSE NERO_BI_WH TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON DATABASE NERO_GOVERNANCE TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.CORTEX_TOOLS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.COST TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA NERO_GOVERNANCE.COST TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA NERO_GOVERNANCE.COST TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON TABLE NERO_GOVERNANCE.COST.WAREHOUSE_BUDGET TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT ON TABLE NERO_GOVERNANCE.SECURITY.FINDINGS_DOCS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT REFERENCES ON SEMANTIC VIEW NERO_GOVERNANCE.CORTEX_TOOLS.GOVERNANCE_SEMANTIC_VIEW TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON CORTEX SEARCH SERVICE NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC TO ROLE NERO_GOVERNANCE_ROLE;
GRANT USAGE ON INTEGRATION NERO_PYPI_ACCESS_INTEGRATION TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT, INSERT, UPDATE ON TABLE NERO_GOVERNANCE.APPS_CHATBOTS.CHAT_CONVERSATIONS TO ROLE NERO_GOVERNANCE_ROLE;
GRANT SELECT, INSERT ON TABLE NERO_GOVERNANCE.APPS_CHATBOTS.CHAT_MESSAGES TO ROLE NERO_GOVERNANCE_ROLE;
