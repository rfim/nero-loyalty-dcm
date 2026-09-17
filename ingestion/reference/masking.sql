-- UNVERIFIED — reference only, not deployed by DCM (lives outside sources/definitions).
-- This Snowflake account's edition does not support masking policies at all —
-- confirmed directly: `CREATE MASKING POLICY ...` errors with
-- "Unsupported feature 'MASKING POLICY'" independent of DCM. This SQL is
-- correct standard syntax; move the DEFINE block into sources/definitions/
-- once run on an edition that supports masking policies (Enterprise+).
--
-- The ALTER TABLE block below is a further MANUAL step even then: DCM's
-- DEFINE MASKING POLICY only creates the policy, not attaching it to a
-- column — that isn't a supported DCM primitive yet.

-- ---- policy definitions ----

DEFINE MASKING POLICY NERO_DB.NERO_LOYALTY.MASK_IDENTIFIER_INTEGER AS (val NUMBER) RETURNS NUMBER ->
    CASE
        WHEN CURRENT_ROLE() IN ('ACCOUNTADMIN') THEN val
        ELSE NULL
    END
COMMENT = 'Masks identifier columns of type integer for any role other than ACCOUNTADMIN.';

-- ---- attach to columns (manual, after the policies above are deployed) ----

ALTER TABLE NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_CUSTOMERS MODIFY COLUMN CUSTOMER_ID SET MASKING POLICY NERO_DB.NERO_LOYALTY.MASK_IDENTIFIER_INTEGER;
ALTER TABLE NERO_DB.NERO_LOYALTY.VALIDATED_LOYALTY_EVENTS MODIFY COLUMN CUSTOMER_ID SET MASKING POLICY NERO_DB.NERO_LOYALTY.MASK_IDENTIFIER_INTEGER;
ALTER TABLE NERO_DB.NERO_LOYALTY.VALIDATED_TRANSACTIONS MODIFY COLUMN CUSTOMER_ID SET MASKING POLICY NERO_DB.NERO_LOYALTY.MASK_IDENTIFIER_INTEGER;
