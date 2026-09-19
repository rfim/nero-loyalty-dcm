with resolved as (
    select
        {{ dbt_utils.generate_surrogate_key(['store_id']) }} as store_key,
        store_id,
        store_name,
        region,
        store_format,
        opened_date
    from {{ ref('silver_stores') }}
),

standard_members as (
    select '-1' as store_key, null as store_id, 'Unknown' as store_name, null as region, null as store_format, null as opened_date
    union all
    select '-2' as store_key, null as store_id, 'Not Applicable' as store_name, null as region, null as store_format, null as opened_date
)

select * from resolved
union all
select * from standard_members
