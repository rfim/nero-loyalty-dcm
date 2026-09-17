-- =============================================================================
-- Least-privilege identity for external MCP clients (Microsoft Copilot
-- Studio's MCP onboarding wizard, or any other MCP client) connecting to
-- NERO_PLATFORM_MCP_SERVER (account_setup/cortex_chatbot_stack.sql).
--
-- Deliberately does NOT create a Programmatic Access Token here.
-- Snowflake requires a network policy on a user before it will issue a
-- PAT, and Microsoft does not publish a stable IP range for Copilot
-- (personal or Copilot Studio) -- it runs on shared, dynamic Azure
-- infrastructure. Guessing a range, or opening a permissive policy just
-- to unblock this, is a security call that belongs to whoever owns this
-- account, not something to default into silently.
--
-- To finish the connection yourself:
--   1. In Snowsight: Admin > Users & Roles > NERO_COPILOT_USER >
--      Programmatic access tokens > Generate new. Snowsight will prompt
--      you the same way for a network policy -- pick what you're
--      comfortable with for this account (a permissive policy is lower
--      stakes on a personal/trial account than a production one).
--   2. In Copilot Studio: Add tool > Model Context Protocol > paste the
--      server URL below, choose "API key" / bearer auth, paste the PAT.
--
-- MCP server URL (verified live via the MCP `initialize` handshake):
--   https://pegszyg-if37362.snowflakecomputing.com/api/v2/databases/
--     NERO_GOVERNANCE/schemas/APPS/mcp-servers/NERO_PLATFORM_MCP_SERVER
--
-- Apply with: snow sql -f account_setup/copilot_mcp_access.sql
-- =============================================================================

CREATE ROLE IF NOT EXISTS NERO_COPILOT_ROLE
  COMMENT = 'Least-privilege role for external Copilot/MCP clients: USAGE on the MCP server and its two underlying tools only, nothing else.';

CREATE USER IF NOT EXISTS NERO_COPILOT_USER
  TYPE = SERVICE
  DEFAULT_ROLE = NERO_COPILOT_ROLE
  DEFAULT_WAREHOUSE = NERO_BI_WH
  COMMENT = 'Service identity for external MCP clients (Microsoft Copilot / Copilot Studio). Authenticates via Programmatic Access Token -- a static bearer credential a low-code tool can actually hold, unlike key-pair JWT.';

GRANT ROLE NERO_COPILOT_ROLE TO USER NERO_COPILOT_USER;

GRANT USAGE ON WAREHOUSE NERO_BI_WH TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON DATABASE NERO_GOVERNANCE TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.CORTEX_TOOLS TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON MCP SERVER NERO_GOVERNANCE.CORTEX_TOOLS.NERO_PLATFORM_MCP_SERVER TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON CORTEX SEARCH SERVICE NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON DATABASE NERO_DB TO ROLE NERO_COPILOT_ROLE;
GRANT USAGE ON SCHEMA NERO_DB.NERO_LOYALTY TO ROLE NERO_COPILOT_ROLE;
GRANT REFERENCES ON SEMANTIC VIEW NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW TO ROLE NERO_COPILOT_ROLE;
