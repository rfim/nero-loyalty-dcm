{#- CI cleanup: drops a PR's isolated DBT_CI_<PR_NUMBER> schema. Not run in
    prod — only from the dbt-ci.yml workflow's teardown step. -#}
{% macro drop_schema_if_exists(schema_name) %}
  {% set drop_sql %}
    drop schema if exists {{ target.database }}.{{ schema_name }} cascade
  {% endset %}
  {% do run_query(drop_sql) %}
  {{ log("Dropped schema " ~ target.database ~ "." ~ schema_name, info=True) }}
{% endmacro %}
