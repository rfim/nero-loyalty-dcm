{% snapshot snap_loyalty_customers %}

{{
    config(
        target_schema='snapshots',
        unique_key='customer_id',
        strategy='check',
        check_cols=['tier', 'home_store_id'],
        invalidate_hard_deletes=true
    )
}}

select
    customer_id,
    signup_date,
    tier,
    home_store_id,
    batch_id
from {{ source('nero_validated', 'loyalty_customers') }}

{% endsnapshot %}
