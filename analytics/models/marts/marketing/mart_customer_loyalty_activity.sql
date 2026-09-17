-- Grain: one row per customer (current tier and lifetime/rolling activity).
with current_customer as (
    select customer_key, customer_id, tier, signup_date
    from {{ ref('dim_customer_scd') }}
    where is_current and customer_id is not null
),

txn_agg as (
    select
        f.customer_key,
        min(d.full_date) as first_transaction_date,
        max(d.full_date) as latest_transaction_date,
        sum(case when d.full_date >= dateadd('day', -30, current_date()) then f.net_sales_amount else 0 end) as sales_last_30d,
        sum(case when d.full_date >= dateadd('day', -60, current_date()) then f.net_sales_amount else 0 end) as sales_last_60d,
        sum(case when d.full_date >= dateadd('day', -90, current_date()) then f.net_sales_amount else 0 end) as sales_last_90d
    from {{ ref('fact_sales_transaction') }} f
    join {{ ref('dim_date') }} d on d.date_key = f.transaction_date_key
    where f.customer_key not in ('-1', '-2')
    group by f.customer_key
),

event_agg as (
    select
        f.customer_key,
        max(d.full_date) as latest_event_date,
        sum(f.earn_count) as earn_count,
        sum(f.redeem_count) as redeem_count
    from {{ ref('fact_loyalty_event') }} f
    join {{ ref('dim_date') }} d on d.date_key = f.event_date_key
    group by f.customer_key
),

base as (
    select
        cc.customer_id,
        cc.tier,
        cc.signup_date,
        txn.first_transaction_date,
        txn.latest_transaction_date,
        evt.latest_event_date,
        coalesce(txn.sales_last_30d, 0) as sales_last_30d,
        coalesce(txn.sales_last_60d, 0) as sales_last_60d,
        coalesce(txn.sales_last_90d, 0) as sales_last_90d,
        coalesce(evt.earn_count, 0) as earn_count,
        coalesce(evt.redeem_count, 0) as redeem_count,
        greatest(
            coalesce(txn.latest_transaction_date, cc.signup_date),
            coalesce(evt.latest_event_date, cc.signup_date)
        ) as latest_activity_date
    from current_customer cc
    left join txn_agg txn on txn.customer_key = cc.customer_key
    left join event_agg evt on evt.customer_key = cc.customer_key
)

select
    customer_id,
    tier,
    signup_date,
    first_transaction_date,
    latest_transaction_date,
    latest_event_date,
    sales_last_30d,
    sales_last_60d,
    sales_last_90d,
    earn_count,
    redeem_count,
    latest_activity_date,
    datediff('day', latest_activity_date, current_date()) as days_since_latest_activity,
    case
        when first_transaction_date is null and latest_event_date is null then 'joined_never_active'
        when datediff('day', latest_activity_date, current_date()) <= 30 then 'active'
        when datediff('day', latest_activity_date, current_date()) <= 90 then 'at_risk'
        else 'dormant'
    end as customer_status
from base
