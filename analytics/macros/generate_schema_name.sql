{#- Standard dbt override: in prod, a model's custom `schema` config is used
    verbatim (so mart_store_daily_performance lands in NERO_ANALYTICS.MART_OPERATIONS,
    not NERO_ANALYTICS.GOLD_MART_OPERATIONS). In every other target (dev, CI),
    everything collapses into target.schema instead — one schema per developer
    or per PR, so `dbt-ci.yml`'s teardown (which only drops target.schema
    itself) actually removes everything a run created, with nothing orphaned. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if target.name == 'prod' and custom_schema_name is not none -%}
        {{ custom_schema_name | trim }}
    {%- else -%}
        {{ target.schema }}
    {%- endif -%}
{%- endmacro %}
