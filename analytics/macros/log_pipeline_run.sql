{#
  on-run-end hook: persists every model's run outcome into
  NERO_DB."04_METADATA".PIPELINE_RUN_LOG -- status, duration, rows
  affected -- so successful-run history and ETL/pipeline performance are
  queryable from Snowflake, not just visible to whoever ran the CLI. Model
  results only (see log_dbt_test_results.sql for the sibling test-result
  log). Also triggers the watermark upsert so both land together at the
  end of the same run. See account_setup/dbt_pipeline_metadata.sql.
#}
{% macro log_pipeline_run(results) %}
  {% if execute %}
    {% set model_results = results | selectattr("node.resource_type", "equalto", "model") | list %}
    {% if model_results | length > 0 %}
      {% set value_rows = [] %}
      {% for r in model_results %}
        {% set message = (r.message or "")[:500] | replace("'", "''") %}
        {% set name = r.node.name | replace("'", "''") %}
        {% set materialization = (r.node.config.materialized or "")  | replace("'", "''") %}
        {% set rows_affected = r.adapter_response.get("rows_affected") if r.adapter_response else none %}
        {% do value_rows.append(
          "('" ~ invocation_id ~ "', '" ~ run_started_at.strftime('%Y-%m-%d %H:%M:%S%z') ~ "', '"
          ~ name ~ "', '" ~ materialization ~ "', '" ~ r.status ~ "', "
          ~ (r.execution_time | round(3)) ~ ", "
          ~ (rows_affected if rows_affected is not none else "NULL") ~ ", '" ~ message ~ "')"
        ) %}
      {% endfor %}
      {% set insert_sql %}
        INSERT INTO NERO_DB."04_METADATA".PIPELINE_RUN_LOG
          (INVOCATION_ID, RUN_STARTED_AT, MODEL_NAME, MATERIALIZATION, STATUS, EXECUTION_TIME, ROWS_AFFECTED, "MESSAGE")
        VALUES {{ value_rows | join(", ") }}
      {% endset %}
      {% do run_query(insert_sql) %}
    {% endif %}
    {% do log_pipeline_watermarks() %}
  {% endif %}
{% endmacro %}
