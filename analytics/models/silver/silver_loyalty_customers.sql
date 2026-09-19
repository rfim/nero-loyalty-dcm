select
    customer_id,
    signup_date,
    tier,
    home_store_id,
    batch_id,
    current_timestamp() as dbt_loaded_at
from {{ ref('bronze_loyalty_customers') }}
