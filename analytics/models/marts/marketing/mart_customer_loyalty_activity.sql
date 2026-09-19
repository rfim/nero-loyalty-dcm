-- Grain: one row per customer (current tier and lifetime/rolling activity).
--
-- PII-safe: exposes customer_key (the DIM_CUSTOMER_SCD surrogate key), never
-- the raw customer_id -- this mart is meant to be readable by Power BI/other
-- external BI tools, which should never see the real identifier.
--
-- Rolling windows aggregate by customer_id (resolved internally via
-- customer_key_to_id, never in the final output), not by the current
-- version's customer_key alone -- a customer who had a tier change carries
-- a *different* customer_key on transactions made under their old tier
-- (each SCD2 version gets its own surrogate key), so joining only on the
-- current version's key would silently drop that history from their
-- rolling totals.
--
-- sales_last_Nd and visits_last_Nd are kept as separate additive columns
-- (not a pre-divided "average basket") so a consumer can compute
-- SUM(sales)/SUM(visits) under any filter/grouping it applies -- by tier,
-- by store, by date range -- and get a correct result. A pre-computed
-- ratio column can't be correctly re-sliced.
with current_customer as (
    select customer_key, customer_id, tier, signup_date
    from {{ ref('dim_customer_scd') }}
    where is_current and customer_id is not null
),

-- No more release pointer to gate against (PROCESS_BATCH retired in favor
-- of dbt models/tests) -- "as of" is just today, since silver now always
-- reflects whatever's currently landed rather than a specific approved,
-- compare-and-set batch.
as_of as (
    select current_date() as reporting_as_of_date
),

txn_agg as (
    select
        f.customer_id,
        min(f.transaction_date) as first_transaction_date,
        max(f.transaction_date) as latest_transaction_date,
        sum(case when f.transaction_date >= dateadd('day', -30, ao.reporting_as_of_date) then f.net_sales_amount else 0 end) as sales_last_30d,
        sum(case when f.transaction_date >= dateadd('day', -30, ao.reporting_as_of_date) then 1 else 0 end) as visits_last_30d,
        sum(case when f.transaction_date >= dateadd('day', -60, ao.reporting_as_of_date) then f.net_sales_amount else 0 end) as sales_last_60d,
        sum(case when f.transaction_date >= dateadd('day', -60, ao.reporting_as_of_date) then 1 else 0 end) as visits_last_60d,
        sum(case when f.transaction_date >= dateadd('day', -90, ao.reporting_as_of_date) then f.net_sales_amount else 0 end) as sales_last_90d,
        sum(case when f.transaction_date >= dateadd('day', -90, ao.reporting_as_of_date) then 1 else 0 end) as visits_last_90d
    from {{ ref('silver_transactions') }} f
    cross join as_of ao
    where f.customer_id is not null
    group by f.customer_id
),

event_agg as (
    select
        f.customer_id,
        max(f.event_date) as latest_event_date,
        count_if(f.event_type = 'earn') as earn_count,
        count_if(f.event_type = 'redeem') as redeem_count
    from {{ ref('silver_loyalty_events') }} f
    group by f.customer_id
),

base as (
    select
        cc.customer_key,
        cc.tier,
        cc.signup_date,
        txn.first_transaction_date,
        txn.latest_transaction_date,
        evt.latest_event_date,
        coalesce(txn.sales_last_30d, 0) as sales_last_30d,
        coalesce(txn.visits_last_30d, 0) as visits_last_30d,
        coalesce(txn.sales_last_60d, 0) as sales_last_60d,
        coalesce(txn.visits_last_60d, 0) as visits_last_60d,
        coalesce(txn.sales_last_90d, 0) as sales_last_90d,
        coalesce(txn.visits_last_90d, 0) as visits_last_90d,
        coalesce(evt.earn_count, 0) as earn_count,
        coalesce(evt.redeem_count, 0) as redeem_count,
        greatest(
            coalesce(txn.latest_transaction_date, cc.signup_date),
            coalesce(evt.latest_event_date, cc.signup_date)
        ) as latest_activity_date
    from current_customer cc
    left join txn_agg txn on txn.customer_id = cc.customer_id
    left join event_agg evt on evt.customer_id = cc.customer_id
)

select
    b.customer_key,
    b.tier,
    b.signup_date,
    b.first_transaction_date,
    b.latest_transaction_date,
    b.latest_event_date,
    b.sales_last_30d,
    b.visits_last_30d,
    b.sales_last_60d,
    b.visits_last_60d,
    b.sales_last_90d,
    b.visits_last_90d,
    b.earn_count,
    b.redeem_count,
    b.latest_activity_date,
    datediff('day', b.latest_activity_date, ao.reporting_as_of_date) as days_since_latest_activity,
    case
        when b.first_transaction_date is null and b.latest_event_date is null then 'joined_never_active'
        when datediff('day', b.latest_activity_date, ao.reporting_as_of_date) <= 30 then 'active'
        when datediff('day', b.latest_activity_date, ao.reporting_as_of_date) <= 90 then 'at_risk'
        else 'dormant'
    end as customer_status
from base b
cross join as_of ao
