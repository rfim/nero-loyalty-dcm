-- =============================================================================
-- Chatbot-specific cost/security views, backing the Chatbot Cost Governance
-- Report and Chatbot Security Governance Report (chatbot_governance_app/) --
-- narrower in scope than the platform-wide reports in governance_app/,
-- which cover warehouse compute and account-wide security, not the
-- chatbot's own Cortex Agent/Analyst/Search/MCP usage specifically.
--
-- Apply with: snow sql -f account_setup/chatbot_governance_views.sql
-- =============================================================================

CREATE OR REPLACE VIEW NERO_GOVERNANCE.COST.CHATBOT_AGENT_CREDITS_DAILY AS
SELECT
    USER_NAME,
    DATE_TRUNC('day', START_TIME) AS USAGE_DATE,
    COUNT(*) AS REQUEST_COUNT,
    SUM(TOKEN_CREDITS) AS CREDITS,
    SUM(TOKENS) AS TOKENS
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY
GROUP BY USER_NAME, USAGE_DATE;

COMMENT ON VIEW NERO_GOVERNANCE.COST.CHATBOT_AGENT_CREDITS_DAILY IS
  'Cortex Agent lite-run credit/token usage by user and day. Nero Assistant (chatbot_app) calls the lite-run API with inline tools rather than referencing NERO_PLATFORM_AGENT by name, so AGENT_NAME is always null on this usage view -- USER_NAME is the real attribution key. A user_name like STPLATSTREAMLIT... is the chatbot app''s own service identity (someone used the Streamlit UI); ENGINEERSUPPORT26 rows are direct/API-level testing.';

CREATE OR REPLACE VIEW NERO_GOVERNANCE.SECURITY.MCP_TOOL_CALL_LOG AS
SELECT
    TIMESTAMP, DISPLAY_NAME, SERVER_CATEGORY, TOOL_NAME, STATUS, USER_NAME, ROLE_NAME, REQUEST_ID
FROM SNOWFLAKE.TRUST_CENTER.MCP_SERVER_EVENTS
ORDER BY TIMESTAMP DESC;

COMMENT ON VIEW NERO_GOVERNANCE.SECURITY.MCP_TOOL_CALL_LOG IS
  'Every MCP tool invocation against NERO_PLATFORM_MCP_SERVER -- who (user/role) called which tool, when, and whether it succeeded. The audit trail for external MCP clients (Copilot, etc.) once connected.';

CREATE OR REPLACE VIEW NERO_GOVERNANCE.SECURITY.MCP_SERVER_REGISTRY AS
SELECT SERVER_NAME, DATABASE_NAME, SCHEMA_NAME, SERVER_TYPE, IS_DISABLED, INTEGRATION, CREATED_ON
FROM SNOWFLAKE.TRUST_CENTER.MCP_SERVERS;

COMMENT ON VIEW NERO_GOVERNANCE.SECURITY.MCP_SERVER_REGISTRY IS
  'Registered MCP servers and whether each is currently enabled -- a quick check that NERO_PLATFORM_MCP_SERVER is live and nothing unexpected has been registered. This view (unlike SHOW MCP SERVERS) lags -- the chatbot_security_report.py app uses SHOW MCP SERVERS live for its actual up/down check and only supplements with this view where richer detail is available.';

GRANT SELECT ON VIEW NERO_GOVERNANCE.COST.CHATBOT_AGENT_CREDITS_DAILY TO ROLE NERO_BI_ROLE;
GRANT SELECT ON VIEW NERO_GOVERNANCE.SECURITY.MCP_TOOL_CALL_LOG TO ROLE NERO_BI_ROLE;
GRANT SELECT ON VIEW NERO_GOVERNANCE.SECURITY.MCP_SERVER_REGISTRY TO ROLE NERO_BI_ROLE;
