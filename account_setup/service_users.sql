
-- =============================================================================
-- Least-privilege service users, one per workload, each with its own
-- key-pair identity, default role, and default warehouse (SnowPro RBAC
-- best practice: no shared logins, no workload running as ACCOUNTADMIN).
-- Apply with: snow sql -f account_setup/service_users.sql
-- =============================================================================

-- ================================ ROLES ======================================

CREATE ROLE IF NOT EXISTS NERO_INGEST_ROLE
  COMMENT = 'Ingestion service role: PROCESS_BATCH/RUN_PENDING execution and LANDING_STAGE writes only. No direct table access beyond RAW_ENVELOPES insert (via COPY INTO).';

CREATE ROLE IF NOT EXISTS NERO_CI_ROLE
  COMMENT = 'CI/CD service role: GitHub Actions. Scoped to what does not require DCM project ownership -- see account_setup/service_users.sql comments for the DCM-deploy caveat.';

CREATE ROLE IF NOT EXISTS NERO_BI_ROLE
  COMMENT = 'Reporting service role: read-only SELECT on validated silver data and governance views. Used by Streamlit apps and the future Power BI reader.';

-- NERO_DBT_ROLE already exists (created in an earlier phase) -- reused as-is.

-- ================================ USERS ======================================
-- All SERVICE type (not PERSON): no password, key-pair auth only, cannot be
-- targeted by password/MFA-readiness scanners the way a person user can.

CREATE USER IF NOT EXISTS NERO_INGEST_USER
  TYPE = SERVICE
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2un4UmBTW6NsOkKpawRu8wMcgvpGXXKhjLoJx50XMOodRvAD0omCksNmrv2mJNcAS6x9xFBo3skXLa7+M0eYgT2g+7YnR3vdxCrng3d2vKh46eP2j9PtpmnevSEaEMAZXpx95eo+kay+mAFA6FtZzkoOMS5sEigmIGLszN6nx/pyD9qEctuJCcpBd1UZQgYTRRkd9WEHSl73OAf/wf9OEArB68uS2dTTtvdw06QSEDkY1qJp4e6exKOl6pUt3j9lm9+4DK+YrFEKpL6KNbH+VLOt+TULPq35cpT/ffz794syzt/pbV3ifk1i6RSl6ICgY879lX1GDKZ9HC++epxZ8QIDAQAB'
  DEFAULT_ROLE = NERO_INGEST_ROLE
  DEFAULT_WAREHOUSE = NERO_LOAD_WH
  DEFAULT_NAMESPACE = 'NERO_DB."00_BRONZE"'
  COMMENT = 'Service identity for ingestion/load_csv_batch.py and the CONTRACT_GATE_TASK-adjacent manual batch loads.';

CREATE USER IF NOT EXISTS NERO_CI_USER
  TYPE = SERVICE
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyTbxBQakqv6HpDf2tAoGWFvXXtYgkNRb1yeALIPPymWdATBQLS/iIcVy6YuWntpjHB2VQ50jBTQAQz8v9cPxoS/a2igNxVeb+f7f2vSvNjt7gqfPyBLb49TUjx035DOCTXkAPk6pOUcAx3a8YEJNcmt23i8J6hrbWBWQrQU4Q/anehoCSaV6XAIO5/sHQaR734G49mcq0jyRgNivqg1SdW5vDQCODO7RBCl9lNwjLEWFJbwWqkmuzd8pb+fIL135Xrzejdh0VjsvMeNRgcQ01JrV4Uw7mkU04QkyR7QVP32Zo8lx+wgBpKnLnfKzOIZQISvQ7OjcAMewyNs0hge5CQIDAQAB'
  DEFAULT_ROLE = NERO_CI_ROLE
  DEFAULT_WAREHOUSE = NERO_CI_WH
  COMMENT = 'Service identity for GitHub Actions (dcm-validate.yml, dcm-deploy.yml, dbt-ci.yml). Not yet wired into the workflows -- see rollout note.';

CREATE USER IF NOT EXISTS NERO_BI_USER
  TYPE = SERVICE
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtj8gY0I7+wxPeIQ+tz/8seu5zXdTFADnOpc7FGIDh7WHMTmLfVLOYFHLfkYcKGaekAjl/dpKC8MG5CyCBACm3gC2deEUzBKodDzqoH7daQdbNQeaVRgFEv6/V0fhueSSsn9HI4fl3CLc8FCZOg0oy82JeVSO6JiSC0iOubLfQzhj+Hq3wIqaTirgll79v9/TY+/t92Kiw+8Pt/Jon079x4w2BlhFJ3SJjCofIXDfDen8qGTi8KRK6AFuV2572VfrR7niH0Vx6xBy6eZfrERJMp0Cz8i87nCCMa8vON7rOrgayusctCdirf/xLzXE5WpuunLCAAzoRRgB764YilqRTQIDAQAB'
  DEFAULT_ROLE = NERO_BI_ROLE
  DEFAULT_WAREHOUSE = NERO_BI_WH
  COMMENT = 'Service identity for reporting/BI readers: Streamlit apps today, a future external Power BI connection tomorrow.';

CREATE USER IF NOT EXISTS NERO_DBT_USER
  TYPE = SERVICE
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv+5UYV6tYuoXQZlB17AqI1Wro/rKWr2mYoFxzYJBiv79Jxv55QnizkRW/8wHdmcd1+OodVJPrsxvL8w/bFBjS24uuRm0AJky7MyVWk9Zy4mGgJdhltkBlOKBqe95x+aM2wxlBmApYxM3iITaKm8skKX4+yDqiHGmd/yjNFZrnl/UBIIns80kb8iIW6l4H5CzBI2+0FtBxpU5rIA41rgU+VgfTy67V5s8uh8+IJVH2kusq2ZyMSCkQVeWwlQn5pwyZaPfqGnzUlGt3G0cFb6Gcx+XSC1r5YpsvzYE8/dJbMrLdWL/qzt9IuR30rkPjvksXce0aF+hy9fLn8wwIJWoDwIDAQAB'
  DEFAULT_ROLE = NERO_DBT_ROLE
  DEFAULT_WAREHOUSE = NERO_DBT_WH
  DEFAULT_NAMESPACE = 'NERO_ANALYTICS'
  COMMENT = 'Service identity for dbt. Existing NERO_DBT_ROLE previously ran under the shared ACCOUNTADMIN login with a role switch; this gives it its own login instead.';

GRANT ROLE NERO_INGEST_ROLE TO USER NERO_INGEST_USER;
GRANT ROLE NERO_CI_ROLE TO USER NERO_CI_USER;
GRANT ROLE NERO_BI_ROLE TO USER NERO_BI_USER;
GRANT ROLE NERO_DBT_ROLE TO USER NERO_DBT_USER;

-- ================================ GRANTS =====================================

-- ---- NERO_INGEST_ROLE: warehouse + exactly what PROCESS_BATCH needs ----
GRANT USAGE ON WAREHOUSE NERO_LOAD_WH TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON DATABASE NERO_DB TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."00_BRONZE" TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."02_CONTROL" TO ROLE NERO_INGEST_ROLE;
GRANT READ, WRITE ON STAGE NERO_DB."00_BRONZE".LANDING_STAGE TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON FILE FORMAT NERO_DB."00_BRONZE".JSON_LINES TO ROLE NERO_INGEST_ROLE;
GRANT INSERT ON TABLE NERO_DB."00_BRONZE".RAW_ENVELOPES TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON PROCEDURE NERO_DB."02_CONTROL".PROCESS_BATCH(VARCHAR) TO ROLE NERO_INGEST_ROLE;
GRANT USAGE ON PROCEDURE NERO_DB."02_CONTROL".RUN_PENDING() TO ROLE NERO_INGEST_ROLE;

-- ---- NERO_CI_ROLE: warehouse usage today; DCM-deploy rights are a known gap ----
-- DCM projects are owned at creation (manifest.yml: project_owner ACCOUNTADMIN).
-- Granting a non-owner role rights to `snow dcm deploy` requires either
-- transferring project ownership or a DCM-specific privilege grant this
-- account's DCM preview does not yet expose cleanly -- untested here on
-- purpose rather than guessed at. dcm-validate.yml / dcm-deploy.yml keep
-- running under the existing ACCOUNTADMIN-backed connection until that is
-- resolved and verified against a live CI run.
GRANT USAGE ON WAREHOUSE NERO_CI_WH TO ROLE NERO_CI_ROLE;
GRANT USAGE ON DATABASE NERO_GOVERNANCE TO ROLE NERO_CI_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.APPS TO ROLE NERO_CI_ROLE;
GRANT CREATE STREAMLIT ON SCHEMA NERO_GOVERNANCE.APPS TO ROLE NERO_CI_ROLE;

-- ---- NERO_BI_ROLE: read-only, silver data + governance views ----
GRANT USAGE ON WAREHOUSE NERO_BI_WH TO ROLE NERO_BI_ROLE;
GRANT USAGE ON DATABASE NERO_DB TO ROLE NERO_BI_ROLE;
GRANT USAGE ON SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_BI_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_BI_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA NERO_DB."01_SILVER" TO ROLE NERO_BI_ROLE;
GRANT USAGE ON DATABASE NERO_GOVERNANCE TO ROLE NERO_BI_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.COST TO ROLE NERO_BI_ROLE;
GRANT USAGE ON SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_BI_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA NERO_GOVERNANCE.COST TO ROLE NERO_BI_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_BI_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA NERO_GOVERNANCE.COST TO ROLE NERO_BI_ROLE;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA NERO_GOVERNANCE.SECURITY TO ROLE NERO_BI_ROLE;
