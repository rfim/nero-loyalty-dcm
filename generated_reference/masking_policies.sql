-- UNVERIFIED — reference only, not deployed by DCM (lives outside sources/definitions).
-- This Snowflake account's edition does not support masking policies at all —
-- confirmed directly: `CREATE MASKING POLICY ...` errors with
-- "Unsupported feature 'MASKING POLICY'" independent of DCM. This SQL is
-- correct standard syntax; move it into sources/definitions/ once run on an
-- edition that supports masking policies (Enterprise+), then also wire
-- generated_reference/attach_masking_policies.sql into a reviewed deploy step.

DEFINE MASKING POLICY NERO_DB.NERO_LOYALTY.MASK_IDENTIFIER_INTEGER AS (val NUMBER) RETURNS NUMBER ->
    CASE
        WHEN CURRENT_ROLE() IN ('ACCOUNTADMIN') THEN val
        ELSE NULL
    END
COMMENT = 'Masks identifier columns of type integer for any role other than ACCOUNTADMIN.';
