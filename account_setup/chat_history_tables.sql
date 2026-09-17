-- =============================================================================
-- Persistent conversation history for Nero Assistant (chatbot_app/), plus
-- the grants that make the app's least-privilege redeploy possible.
--
-- Apply with: snow sql -f account_setup/chat_history_tables.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS NERO_GOVERNANCE.APPS.CHAT_CONVERSATIONS (
    CONVERSATION_ID VARCHAR(64) NOT NULL,
    USER_NAME       VARCHAR(200) NOT NULL,
    TITLE           VARCHAR(500),
    CREATED_AT      TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT      TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (CONVERSATION_ID)
)
COMMENT = 'Nero Assistant conversation index, one row per conversation. Scoped per USER_NAME so each Snowflake login only ever sees its own history.';

CREATE TABLE IF NOT EXISTS NERO_GOVERNANCE.APPS.CHAT_MESSAGES (
    MESSAGE_ID      VARCHAR(64) DEFAULT UUID_STRING(),
    CONVERSATION_ID VARCHAR(64) NOT NULL,
    ROLE            VARCHAR(20) NOT NULL,
    CONTENT         VARCHAR(16777216) NOT NULL,
    CREATED_AT      TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (MESSAGE_ID)
)
COMMENT = 'Nero Assistant message history, also the audit trail of every question asked and answer given.';

-- Deliberately no DELETE grant: chatbot_app/chat_store.py never deletes
-- conversations (no delete feature in the UI), so the running role
-- doesn't get a privilege it has no use for.
GRANT SELECT, INSERT, UPDATE ON TABLE NERO_GOVERNANCE.APPS.CHAT_CONVERSATIONS TO ROLE NERO_BI_ROLE;
GRANT SELECT, INSERT ON TABLE NERO_GOVERNANCE.APPS.CHAT_MESSAGES TO ROLE NERO_BI_ROLE;

-- ============================ LEAST-PRIVILEGE APP OWNERSHIP ==================
-- Nero Assistant was originally created under ACCOUNTADMIN (whoever ran
-- `snow streamlit deploy` first), which meant Cortex Analyst's generated
-- SQL -- and anything else the app's own code runs -- executed with full
-- account-admin rights. `GRANT OWNERSHIP ON STREAMLIT` is not a supported
-- operation in Snowflake, so the only way to change the owning role is to
-- drop and recreate the app under a session already authenticated as the
-- target role. That's why chatbot_app/ is deployed with
-- `-c nero_bi_test` (a connection as NERO_BI_USER / NERO_BI_ROLE) instead
-- of the default ACCOUNTADMIN-backed connection used everywhere else in
-- this repo -- see chatbot_app/README-ish note in snowflake.yml's
-- surrounding docs. NERO_BI_ROLE has no DDL/DML grants anywhere except
-- the two tables above, so even a successful prompt-injection against the
-- agent's generated SQL can only ever SELECT.
--
-- These two grants were missing from account_setup/service_users.sql and
-- are required for that redeploy path to work at all:
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.APPS TO ROLE NERO_BI_ROLE;
GRANT CREATE STREAMLIT ON SCHEMA NERO_GOVERNANCE.APPS TO ROLE NERO_BI_ROLE;
GRANT USAGE ON INTEGRATION NERO_PYPI_ACCESS_INTEGRATION TO ROLE NERO_BI_ROLE;
