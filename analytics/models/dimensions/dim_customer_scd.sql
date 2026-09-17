with resolved as (
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
),

standard_members as (
    select
        '-1' as customer_key, null as customer_id, null as signup_date, null as tier,
        null as home_store_id, cast('1900-01-01' as timestamp_ntz) as valid_from,
        cast('9999-12-31' as timestamp_ntz) as valid_to, true as is_current,
        'standard_member' as tier_effective_source, 'not_applicable' as tier_effective_precision
    union all
    select
        '-2', null, null, null,
        null, cast('1900-01-01' as timestamp_ntz),
        cast('9999-12-31' as timestamp_ntz), true,
        'standard_member', 'not_applicable'
)

select * from resolved
union all
select * from standard_members
