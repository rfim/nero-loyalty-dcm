-- =============================================================================
-- Fixes "SQL compilation error: Secret 'secret from configuration' does not
-- exist or not authorized" when pulling in the Snowsight Workspace linked to
-- this (private) repo.
--
-- Root cause, confirmed via SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY: on
-- 2026-09-16 the original setup created NERO_DB.NERO_LOYALTY.GITHUB_SECRET
-- and NERO_DB.NERO_LOYALTY.NERO_LOYALTY_REPO, then bound the Workspace to
-- that exact secret via SYSTEM$WORKSPACE_CREATE_WITH_REPO. Later,
-- NERO_DB.NERO_LOYALTY was dropped during an unrelated schema cleanup
-- (SHOW SEMANTIC VIEWS/AGENTS/MCP SERVERS were checked before dropping it;
-- SHOW SECRETS and SHOW GIT REPOSITORIES were not), which silently took the
-- credential and the git repository object with it. NERO_GITHUB_INTEGRATION
-- itself survived (it lives outside that schema) but its
-- ALLOWED_AUTHENTICATION_SECRETS pointed at an object that no longer
-- existed -- hence the error.
--
-- This recreates both objects at their ORIGINAL fully-qualified names, so
-- the Workspace's existing binding resolves without needing to be
-- reconfigured in Snowsight at all.
--
-- This file holds a real credential when filled in -- never commit a
-- filled-in copy. USERNAME should be your GitHub username; PASSWORD a PAT
-- with `repo` scope (classic) or Contents: Read-only (fine-grained, scoped
-- to just this repository) for github.com/rfim/nero-loyalty-dcm. Generate
-- one at https://github.com/settings/tokens, or reuse `gh auth token` if
-- you are already authenticated as the right account:
--   snow sql -c <connection> -q "
--   CREATE OR REPLACE SECRET NERO_DB.NERO_LOYALTY.GITHUB_SECRET
--     TYPE = PASSWORD
--     USERNAME = '<your-github-username>'
--     PASSWORD = '$(gh auth token)'
--     COMMENT = 'GitHub token for Workspaces git integration.';
--   "
-- Run that CREATE SECRET step yourself, directly -- this project's own
-- guardrails (and general good practice) block an agent from materializing
-- or writing a live credential on your behalf. Everything below this line
-- has no credential in it and is safe to apply normally once the secret
-- above exists:
--   snow sql -f account_setup/github_git_integration.sql -c <connection>
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE SCHEMA IF NOT EXISTS NERO_DB.NERO_LOYALTY
  COMMENT = 'Holds the GitHub secret/git repository for the Snowsight Workspace git connection. Recreated after being accidentally dropped -- see this file''s header. Does not hold LOYALTY_SEMANTIC_VIEW anymore (moved to NERO_GOVERNANCE.CORTEX_TOOLS).';

-- Run the CREATE OR REPLACE SECRET NERO_DB.NERO_LOYALTY.GITHUB_SECRET
-- command from the header comment yourself before continuing past this
-- point -- the two statements below fail if that secret does not exist yet.

ALTER API INTEGRATION NERO_GITHUB_INTEGRATION
  SET ALLOWED_AUTHENTICATION_SECRETS = (NERO_DB.NERO_LOYALTY.GITHUB_SECRET);

CREATE OR REPLACE GIT REPOSITORY NERO_DB.NERO_LOYALTY.NERO_LOYALTY_REPO
  API_INTEGRATION = NERO_GITHUB_INTEGRATION
  GIT_CREDENTIALS = NERO_DB.NERO_LOYALTY.GITHUB_SECRET
  ORIGIN = 'https://github.com/rfim/nero-loyalty-dcm.git'
  COMMENT = 'nero-loyalty-dcm repo, for Snowsight Workspaces.';

-- Confirms the credential actually works before you go back to Snowsight.
ALTER GIT REPOSITORY NERO_DB.NERO_LOYALTY.NERO_LOYALTY_REPO FETCH;

SHOW GIT BRANCHES IN NERO_DB.NERO_LOYALTY.NERO_LOYALTY_REPO;
