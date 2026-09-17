select
    transaction_id,
    store_id,
    customer_id,
    transaction_ts,
    convert_timezone(
        '{{ var("reporting_timezone") }}',
        transaction_ts
    )::date as transaction_date,
    basket_total as net_sales_amount,
    item_count,
    payment_type,
    batch_id,
    current_timestamp() as dbt_loaded_at
from {{ source('nero_validated', 'transactions') }}
