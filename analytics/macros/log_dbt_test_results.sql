{#
  on-run-end hook: persists every test's outcome into a real Snowflake
  table (NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS), so the combined
  governance dashboard's Data Quality tab has something real to show --
  before this, `dbt build`'s pass/fail only ever reached whoever ran the
  CLI. Model/snapshot results are skipped on purpose; this is a *test*
  audit log, not a full run log (see account_setup/dbt_quality_log.sql for
  the table + why).
#}
{% macro log_dbt_test_results(results) %}
  {% if execute %}
    {% set test_results = results | selectattr("node.resource_type", "equalto", "test") | list %}
    {% if test_results | length > 0 %}
      {% set value_rows = [] %}
      {% for r in test_results %}
        {% set failures = r.failures if r.failures is not none else none %}
        {% set message = (r.message or "")[:500] | replace("'", "''") %}
        {% set name = r.node.name | replace("'", "''") %}
        {% do value_rows.append(
          "('" ~ invocation_id ~ "', '" ~ run_started_at.strftime('%Y-%m-%d %H:%M:%S%z') ~ "', '"
          ~ name ~ "', '" ~ r.status ~ "', "
          ~ (failures if failures is not none else "NULL") ~ ", "
          ~ (r.execution_time | round(3)) ~ ", '" ~ message ~ "')"
        ) %}
      {% endfor %}
      {% set insert_sql %}
        INSERT INTO NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS
          (INVOCATION_ID, RUN_STARTED_AT, TEST_NAME, STATUS, FAILURES, EXECUTION_TIME, "MESSAGE")
        VALUES {{ value_rows | join(", ") }}
      {% endset %}
      {% do run_query(insert_sql) %}
    {% endif %}
  {% endif %}
{% endmacro %}
