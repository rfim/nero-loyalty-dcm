-- Grain: one row per store and calendar date with any activity (sales or
-- loyalty events) — not a full date-spine cross join, to avoid generating
-- thousands of empty rows for dates/stores with no data.
with sales as (
    select
        f.transaction_date_key,
        f.store_key,
        count(*) as total_transactions,
        sum(f.net_sales_amount) as net_sales,
        sum(f.loyalty_identified_transaction_count) as identified_loyalty_transactions,
        count(distinct case when f.customer_key not in ('-1', '-2') then f.customer_key end) as distinct_loyalty_customers
    from {{ ref('fact_sales_transaction') }} f
    where f.store_key not in ('-1', '-2')
    group by 1, 2
),

events as (
    select
        f.event_date_key,
        f.store_key,
        sum(f.signup_count) as signups,
        sum(f.earn_count) as earn_events,
        sum(f.redeem_count) as redemptions,
        sum(f.tier_change_count) as tier_changes
    from {{ ref('fact_loyalty_event') }} f
    where f.store_key not in ('-1', '-2')
    group by 1, 2
),

combined_keys as (
    select transaction_date_key as date_key, store_key from sales
    union
    select event_date_key as date_key, store_key from events
)

select
    d.full_date as performance_date,
    s.store_id,
    s.store_name,
    s.region,
    coalesce(sales.total_transactions, 0) as total_transactions,
    coalesce(sales.net_sales, 0) as net_sales,
    coalesce(sales.net_sales, 0) / nullif(sales.total_transactions, 0) as average_basket,
    coalesce(sales.identified_loyalty_transactions, 0) as identified_loyalty_transactions,
    coalesce(sales.identified_loyalty_transactions, 0) / nullif(sales.total_transactions, 0) as loyalty_scan_rate,
    coalesce(sales.distinct_loyalty_customers, 0) as distinct_loyalty_customers,
    coalesce(events.signups, 0) as signups,
    coalesce(events.earn_events, 0) as earn_events,
    coalesce(events.redemptions, 0) as redemptions,
    coalesce(events.tier_changes, 0) as tier_changes
from combined_keys ck
join {{ ref('dim_date') }} d on d.date_key = ck.date_key
join {{ ref('dim_store') }} s on s.store_key = ck.store_key
left join sales on sales.transaction_date_key = ck.date_key and sales.store_key = ck.store_key
left join events on events.event_date_key = ck.date_key and events.store_key = ck.store_key
