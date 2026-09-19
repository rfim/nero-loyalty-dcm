{#
  on-run-end hook: upserts the current incremental watermark (latest
  ingested_at published to bronze, and the row count at that point) per
  dataset into NERO_DB."04_METADATA".PIPELINE_WATERMARKS -- a stored fact,
  refreshed every dbt build, as opposed to DATASET_FRESHNESS (a view that
  recomputes live). See account_setup/dbt_pipeline_metadata.sql.
#}
{% macro log_pipeline_watermarks() %}
  {% if execute %}
    {% set merge_sql %}
      MERGE INTO NERO_DB."04_METADATA".PIPELINE_WATERMARKS AS tgt
      USING (
        SELECT 'STORES' AS DATASET, MAX(INGESTED_AT) AS WATERMARK_VALUE, COUNT(*) AS ROWS_AT_WATERMARK
          FROM NERO_DB."01_BRONZE".BRONZE_STORES
        UNION ALL
        SELECT 'LOYALTY_CUSTOMERS', MAX(INGESTED_AT), COUNT(*)
          FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_CUSTOMERS
        UNION ALL
        SELECT 'TRANSACTIONS', MAX(INGESTED_AT), COUNT(*)
          FROM NERO_DB."01_BRONZE".BRONZE_TRANSACTIONS
        UNION ALL
        SELECT 'LOYALTY_EVENTS', MAX(INGESTED_AT), COUNT(*)
          FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_EVENTS
      ) AS src
      ON tgt.DATASET = src.DATASET
      WHEN MATCHED THEN UPDATE SET
        WATERMARK_COLUMN = 'INGESTED_AT',
        WATERMARK_VALUE = src.WATERMARK_VALUE,
        ROWS_AT_WATERMARK = src.ROWS_AT_WATERMARK,
        UPDATED_AT = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT (DATASET, WATERMARK_COLUMN, WATERMARK_VALUE, ROWS_AT_WATERMARK, UPDATED_AT)
        VALUES (src.DATASET, 'INGESTED_AT', src.WATERMARK_VALUE, src.ROWS_AT_WATERMARK, CURRENT_TIMESTAMP())
    {% endset %}
    {% do run_query(merge_sql) %}
  {% endif %}
{% endmacro %}
