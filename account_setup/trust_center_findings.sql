-- =============================================================================
-- Horizon / Trust Center findings — wraps SNOWFLAKE.TRUST_CENTER.FINDINGS
-- (Snowflake's built-in security scanner: MFA readiness, admin-grant
-- changes, malicious-IP logins, share exposure, etc.) into
-- NERO_GOVERNANCE.SECURITY, alongside the login/grants views already there.
--
-- Apply with: snow sql -f account_setup/trust_center_findings.sql
-- =============================================================================

USE DATABASE NERO_GOVERNANCE;

CREATE OR REPLACE VIEW SECURITY.OPEN_FINDINGS AS
SELECT
    SCANNER_NAME, SEVERITY, STATE, TOTAL_AT_RISK_COUNT,
    RISK_DESCRIPTION, SUGGESTED_ACTION, CREATED_ON, STATE_LAST_MODIFIED_ON
FROM SNOWFLAKE.TRUST_CENTER.FINDINGS
WHERE STATE = 'Open'
ORDER BY
    CASE SEVERITY WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END,
    CREATED_ON DESC;

COMMENT ON VIEW SECURITY.OPEN_FINDINGS IS
  'Unresolved Trust Center scanner findings, worst severity first — the account''s current open security/governance violations.';

CREATE OR REPLACE VIEW SECURITY.FINDINGS_SUMMARY AS
SELECT
    SEVERITY, STATE, COUNT(*) AS FINDING_COUNT, SUM(TOTAL_AT_RISK_COUNT) AS TOTAL_AT_RISK
FROM SNOWFLAKE.TRUST_CENTER.FINDINGS
GROUP BY SEVERITY, STATE;

COMMENT ON VIEW SECURITY.FINDINGS_SUMMARY IS
  'Finding counts by severity x state (Open/Resolved) — the shape of the account''s security posture over time, not just the current open list.';
