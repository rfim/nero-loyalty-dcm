select
    {{ dbt_utils.generate_surrogate_key([
        'customer_id',
        'dbt_valid_from'
    ]) }} as customer_key,

    customer_id,
    signup_date,
    tier,
    home_store_id,
    dbt_valid_from as valid_from,

    coalesce(
        dbt_valid_to,
        '9999-12-31 00:00:00 +00:00'::timestamp_tz
    ) as valid_to,

    dbt_valid_to is null as is_current,
    'snapshot_observed' as tier_effective_source,
    'unknown_between_dbt_runs' as tier_effective_precision

from {{ ref('snap_loyalty_customers') }}
