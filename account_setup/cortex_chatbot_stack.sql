-- =============================================================================
-- Cortex chatbot stack: semantic view (Cortex Analyst), a materialized
-- search corpus + Cortex Search Service, a Cortex Agent tying both together,
-- and an MCP Server exposing the same two tools to external MCP clients.
--
-- Apply with a Python session, not `snow sql -f`:
--   python3 -c "
--   from snowflake.connector import connect
--   conn = connect(connection_name='<your connection>')  # or explicit account/user/key
--   for cur in conn.execute_string(open('account_setup/cortex_chatbot_stack.sql').read()):
--       print(cur.query.splitlines()[0])
--   "
-- `snow sql -f` (and -q) fail on this file specifically with
-- "SQL template rendering error: 'A' is undefined" -- the CLI's Jinja-based
-- SQL templating chokes on the embedded dollar-quoted JSON specs for
-- unclear reasons (a minimal single-JSON file works fine via -f; this
-- multi-block file does not). Filed as a known CLI limitation, not a SQL
-- issue -- every statement here was verified to run cleanly via the plain
-- Python connector.
-- =============================================================================

-- ============================ SEMANTIC VIEW ==================================
-- Cortex Analyst target: structured NL-to-SQL over the loyalty silver tables.
-- CUSTOMERS.HOME_STORE_ID is exposed as a plain dimension, not a join
-- relationship -- a second STORES join path (via CUSTOMERS) alongside the
-- direct TRANSACTIONS->STORES join makes the graph multi-path, which
-- semantic views reject ("Invalid dimension specified: Multi-path
-- relationship... not supported").

CREATE OR REPLACE SEMANTIC VIEW NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW
  TABLES (
    STORES AS NERO_DB."01_SILVER".VALIDATED_STORES PRIMARY KEY (STORE_ID)
      COMMENT = 'The 12 Caffe Nero store locations.',
    CUSTOMERS AS NERO_DB."01_SILVER".VALIDATED_LOYALTY_CUSTOMERS PRIMARY KEY (CUSTOMER_ID)
      COMMENT = 'Enrolled loyalty customers, with their current tier.',
    TRANSACTIONS AS NERO_DB."01_SILVER".VALIDATED_TRANSACTIONS PRIMARY KEY (TRANSACTION_ID)
      COMMENT = 'POS transactions, 90-day window. CUSTOMER_ID is null for walk-in (non-loyalty) baskets.',
    EVENTS AS NERO_DB."01_SILVER".VALIDATED_LOYALTY_EVENTS PRIMARY KEY (EVENT_ID)
      COMMENT = 'Loyalty events: signup, earn, redeem, tier_change.'
  )
  RELATIONSHIPS (
    TRANSACTIONS_TO_STORES AS TRANSACTIONS(STORE_ID) REFERENCES STORES(STORE_ID),
    TRANSACTIONS_TO_CUSTOMERS AS TRANSACTIONS(CUSTOMER_ID) REFERENCES CUSTOMERS(CUSTOMER_ID),
    EVENTS_TO_STORES AS EVENTS(STORE_ID) REFERENCES STORES(STORE_ID)
  )
  FACTS (
    TRANSACTIONS.BASKET_TOTAL AS BASKET_TOTAL COMMENT = 'Basket value in GBP for one transaction.',
    TRANSACTIONS.ITEM_COUNT AS ITEM_COUNT COMMENT = 'Number of items in one transaction.'
  )
  DIMENSIONS (
    STORES.STORE_NAME AS STORE_NAME,
    STORES.REGION AS REGION,
    STORES.FORMAT AS FORMAT,
    CUSTOMERS.TIER AS TIER COMMENT = 'Bronze, Silver or Gold -- the customer''s CURRENT tier, not historical.',
    CUSTOMERS.SIGNUP_DATE AS SIGNUP_DATE,
    CUSTOMERS.HOME_STORE_ID AS HOME_STORE_ID,
    EVENTS.EVENT_TYPE AS EVENT_TYPE COMMENT = 'signup, earn, redeem or tier_change.',
    EVENTS.EVENT_TS AS EVENT_TS,
    TRANSACTIONS.PAYMENT_TYPE AS PAYMENT_TYPE,
    TRANSACTIONS.TRANSACTION_TS AS TRANSACTION_TS
  )
  METRICS (
    TRANSACTIONS.TOTAL_SALES AS SUM(TRANSACTIONS.BASKET_TOTAL) COMMENT = 'Total GBP sales.',
    TRANSACTIONS.AVG_BASKET AS AVG(TRANSACTIONS.BASKET_TOTAL) COMMENT = 'Average basket value in GBP.',
    TRANSACTIONS.TRANSACTION_COUNT AS COUNT(TRANSACTIONS.TRANSACTION_ID) COMMENT = 'Number of transactions.',
    EVENTS.EVENT_COUNT AS COUNT(EVENTS.EVENT_ID) COMMENT = 'Number of loyalty events.',
    CUSTOMERS.CUSTOMER_COUNT AS COUNT(CUSTOMERS.CUSTOMER_ID) COMMENT = 'Number of enrolled customers.'
  )
  COMMENT = 'Nero loyalty & sales semantic model for Cortex Analyst -- ask natural-language questions about stores, customers, transactions and loyalty events.';

-- ========================= SEARCH CORPUS + SERVICE ============================
-- Cortex Search needs change tracking on a real table it owns the refresh
-- of -- SNOWFLAKE.TRUST_CENTER.FINDINGS is a shared system view, so the
-- corpus is materialized here first. Re-run the INSERT block periodically
-- (or wrap in a task) to keep it current; TARGET_LAG governs the search
-- index's own refresh off this table, not the table's own freshness.

CREATE TABLE IF NOT EXISTS NERO_GOVERNANCE.SECURITY.FINDINGS_DOCS (
    FINDING_ID       VARCHAR(64) DEFAULT UUID_STRING(),
    SCANNER_NAME     VARCHAR(200),
    SEVERITY         VARCHAR(20),
    STATE            VARCHAR(20),
    RISK_DESCRIPTION VARCHAR(16777216),
    SUGGESTED_ACTION VARCHAR(16777216),
    CREATED_ON       TIMESTAMP_LTZ
)
CHANGE_TRACKING = TRUE
COMMENT = 'Materialized, searchable copy of SNOWFLAKE.TRUST_CENTER.FINDINGS -- Cortex Search Service source.';

DELETE FROM NERO_GOVERNANCE.SECURITY.FINDINGS_DOCS;

INSERT INTO NERO_GOVERNANCE.SECURITY.FINDINGS_DOCS (SCANNER_NAME, SEVERITY, STATE, RISK_DESCRIPTION, SUGGESTED_ACTION, CREATED_ON)
SELECT SCANNER_NAME, SEVERITY, STATE, RISK_DESCRIPTION, SUGGESTED_ACTION, CREATED_ON
FROM SNOWFLAKE.TRUST_CENTER.FINDINGS;

CREATE OR REPLACE CORTEX SEARCH SERVICE NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC
  ON RISK_DESCRIPTION
  ATTRIBUTES SCANNER_NAME, SEVERITY, STATE, SUGGESTED_ACTION
  WAREHOUSE = NERO_BI_WH
  TARGET_LAG = '1 day'
  AS (
    SELECT FINDING_ID, SCANNER_NAME, SEVERITY, STATE, RISK_DESCRIPTION, SUGGESTED_ACTION, CREATED_ON
    FROM NERO_GOVERNANCE.SECURITY.FINDINGS_DOCS
  );

-- ================================ AGENT =======================================
-- A real Cortex Agent (not a hand-rolled router): orchestrates between the
-- semantic view (structured Q&A) and the search service (findings Q&A).
-- Verified live: both tools called correctly and produced accurate,
-- cited answers end-to-end via the agent's lite-run API.

CREATE OR REPLACE AGENT NERO_GOVERNANCE.APPS.NERO_PLATFORM_AGENT
COMMENT = 'Chatbot agent for the Nero platform: structured loyalty/sales Q&A via Cortex Analyst, security/Trust Center Q&A via Cortex Search.'
FROM SPECIFICATION $$
{
  "models": {"orchestration": "auto"},
  "instructions": {
    "response": "You are the Nero Platform assistant. Answer questions about loyalty/sales data using the loyalty_analyst tool, and questions about security/Trust Center findings using the findings_search tool. Be concise and cite concrete numbers.",
    "orchestration": "Use loyalty_analyst for questions about stores, customers, transactions, baskets, or loyalty events. Use findings_search for questions about security findings, Trust Center, MFA, network policy, or compliance."
  },
  "tools": [
    {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "loyalty_analyst"}},
    {"tool_spec": {"type": "cortex_search", "name": "findings_search"}}
  ],
  "tool_resources": {
    "loyalty_analyst": {
      "semantic_view": "NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW",
      "execution_environment": {"type": "warehouse", "warehouse": "NERO_BI_WH"}
    },
    "findings_search": {"name": "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC", "max_results": 5}
  }
}
$$;

-- =============================== MCP SERVER ===================================
-- Exposes the same two tools over the Model Context Protocol, for external
-- MCP clients (Cortex Code CLI, Claude Desktop, etc.) -- not just the
-- Streamlit chatbot in chatbot_app/.

CREATE OR REPLACE MCP SERVER NERO_GOVERNANCE.APPS.NERO_PLATFORM_MCP_SERVER
COMMENT = 'MCP server exposing Nero loyalty data (Cortex Analyst) and security findings (Cortex Search) as tools for external MCP clients.'
FROM SPECIFICATION $$
{
  "tools": [
    {"type": "CORTEX_SEARCH_SERVICE_QUERY", "identifier": "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC",
     "name": "search_security_findings", "description": "Search Trust Center security/governance findings"},
    {"type": "CORTEX_ANALYST_MESSAGE", "identifier": "NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW",
     "name": "query_loyalty_data", "description": "Ask natural-language questions about Nero loyalty/sales data"}
  ]
}
$$;

-- ================================ GRANTS ======================================
-- NERO_BI_ROLE already reads 01_SILVER + governance views (service_users.sql);
-- extend it to the new Cortex objects so the chatbot (or any future reader
-- under that role) can actually use them.

GRANT USAGE ON DATABASE NERO_DB TO ROLE NERO_BI_ROLE;
GRANT USAGE ON SCHEMA NERO_DB.NERO_LOYALTY TO ROLE NERO_BI_ROLE;
GRANT REFERENCES ON SEMANTIC VIEW NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW TO ROLE NERO_BI_ROLE;
GRANT USAGE ON CORTEX SEARCH SERVICE NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC TO ROLE NERO_BI_ROLE;
GRANT SELECT ON TABLE NERO_GOVERNANCE.SECURITY.FINDINGS_DOCS TO ROLE NERO_BI_ROLE;
GRANT USAGE ON AGENT NERO_GOVERNANCE.APPS.NERO_PLATFORM_AGENT TO ROLE NERO_BI_ROLE;
