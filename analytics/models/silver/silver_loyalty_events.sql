select
    event_id,
    customer_id,
    store_id,
    event_ts,
    convert_timezone(
        '{{ var("reporting_timezone") }}',
        event_ts
    )::date as event_date,
    event_type,
    reward_id,
    batch_id,
    current_timestamp() as dbt_loaded_at
from {{ ref('bronze_loyalty_events') }}
