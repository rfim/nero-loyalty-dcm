-- =============================================================================
-- PyPI-only external access for governance_app Streamlit apps.
--
-- Streamlit apps in this account run on the newer Container Runtime
-- (compute_pool-backed, runtime_name SYSTEM$ST_CONTAINER_RUNTIME_PY3_11), not
-- the classic Warehouse Runtime. That runtime ignores environment.yml
-- entirely -- packages come exclusively from `uv install`, reading
-- requirements.txt/pyproject.toml, which needs real internet access. By
-- default Container Runtime containers have no external network at all,
-- so pip installs fail with a DNS resolution error until an External
-- Access Integration is attached.
--
-- Scoped narrowly to pypi.org + files.pythonhosted.org (the two hosts pip
-- actually needs) -- nothing else. Apply with:
--   snow sql -f account_setup/pypi_external_access.sql
-- =============================================================================

CREATE NETWORK RULE IF NOT EXISTS NERO_GOVERNANCE.APPS.PYPI_NETWORK_RULE
  TYPE = HOST_PORT
  MODE = EGRESS
  VALUE_LIST = ('pypi.org', 'files.pythonhosted.org')
  COMMENT = 'Egress allowlist for pip package installs in Container Runtime Streamlit apps -- PyPI only, nothing else.';

CREATE EXTERNAL ACCESS INTEGRATION IF NOT EXISTS NERO_PYPI_ACCESS_INTEGRATION
  ALLOWED_NETWORK_RULES = (NERO_GOVERNANCE.APPS.PYPI_NETWORK_RULE)
  ENABLED = TRUE
  COMMENT = 'Lets governance_app Streamlit apps pip-install reportlab/xlsxwriter at deploy time. Scoped to PyPI hosts only via PYPI_NETWORK_RULE.';

ALTER STREAMLIT NERO_GOVERNANCE.APPS.COST_GOVERNANCE_REPORT
  SET EXTERNAL_ACCESS_INTEGRATIONS = (NERO_PYPI_ACCESS_INTEGRATION);

ALTER STREAMLIT NERO_GOVERNANCE.APPS.SECURITY_GOVERNANCE_REPORT
  SET EXTERNAL_ACCESS_INTEGRATIONS = (NERO_PYPI_ACCESS_INTEGRATION);
