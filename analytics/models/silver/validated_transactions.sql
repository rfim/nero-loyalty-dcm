-- Incremental by design: transactions are append-only (a POS transaction
-- never mutates once written), so unlike the other 3 datasets this reads
-- only bronze rows newer than what's already here (is_incremental() filter
-- against this table's own max ingested_at), MERGEd in by transaction_id.
-- This is dbt's native replacement for the hand-built MERGE branch
-- PROCESS_BATCH used to run for this dataset's load_mode="incremental".
{{ config(materialized='incremental', unique_key='transaction_id') }}

select
    t.transaction_id,
    t.store_id,
    t.transaction_ts,
    t.customer_id,
    t.basket_total,
    t.item_count,
    t.payment_type,
    t.batch_id,
    t.ingested_at
from {{ source('nero_bronze', 'transactions') }} t
where t.transaction_id is not null
  and t.store_id is not null
  and t.transaction_ts is not null
  and t.basket_total is not null
  and t.item_count is not null
  and t.payment_type in ('Card', 'App', 'Cash')
  and exists (select 1 from {{ ref('validated_stores') }} s where s.store_id = t.store_id)
  and (
    t.customer_id is null
    or exists (select 1 from {{ ref('validated_loyalty_customers') }} c where c.customer_id = t.customer_id)
  )
{% if is_incremental() %}
  and t.ingested_at > (select coalesce(max(ingested_at), '1900-01-01'::timestamp_tz) from {{ this }})
{% endif %}
qualify row_number() over (partition by t.transaction_id order by t.ingested_at desc) = 1
