-- Fails if mart_store_daily_performance's totals drift from
-- fact_sales_transaction (excluding the unresolved-store standard members,
-- which the mart deliberately excludes too).
with mart as (
    select sum(net_sales) as total_sales, sum(total_transactions) as total_txns
    from {{ ref('mart_store_daily_performance') }}
),
fact as (
    select sum(net_sales_amount) as total_sales, count(*) as total_txns
    from {{ ref('fact_sales_transaction') }}
    where store_key not in ('-1', '-2')
)
select mart.total_sales, fact.total_sales, mart.total_txns, fact.total_txns
from mart
join fact
    on abs(mart.total_sales - fact.total_sales) > 0.01
    or mart.total_txns != fact.total_txns
