{% snapshot snap_loyalty_customers %}

{{
    config(
        target_schema='01_SNAPSHOTS',
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
from {{ ref('validated_loyalty_customers') }}

{% endsnapshot %}
