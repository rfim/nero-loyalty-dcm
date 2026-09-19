select
    store_id,
    store_name,
    region,
    format as store_format,
    opened_date,
    batch_id,
    current_timestamp() as dbt_loaded_at
from {{ ref('bronze_stores') }}
