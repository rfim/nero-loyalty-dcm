-- Fails if fact_sales_transaction's count or total net sales drift from
-- staging (no fan-out, no dropped rows).
with staging as (
    select count(*) as txn_count, sum(net_sales_amount) as total_sales
    from {{ ref('stg_transactions') }}
),
fact as (
    select count(*) as txn_count, sum(net_sales_amount) as total_sales
    from {{ ref('fact_sales_transaction') }}
)
select staging.txn_count, fact.txn_count, staging.total_sales, fact.total_sales
from staging
join fact
    on staging.txn_count != fact.txn_count
    or abs(staging.total_sales - fact.total_sales) > 0.01
