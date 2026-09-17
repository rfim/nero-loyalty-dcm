-- Grain: one row per transaction_id (degenerate dimension).
with txn as (
    select * from {{ ref('stg_transactions') }}
),

matched as (
    select
        txn.transaction_id,
        txn.net_sales_amount,
        txn.item_count,
        txn.batch_id,
        txn.customer_id,

        date_dim.date_key as transaction_date_key,
        time_dim.time_key as transaction_time_key,
        coalesce(store_dim.store_key, '-1') as store_key,
        customer_dim.customer_key

    from txn
    left join {{ ref('dim_date') }} date_dim
        on date_dim.full_date = txn.transaction_date
    left join {{ ref('dim_time') }} time_dim
        on time_dim.hour_24 = hour(txn.transaction_ts)
       and time_dim.minute = minute(txn.transaction_ts)
    left join {{ ref('dim_store') }} store_dim
        on store_dim.store_id = txn.store_id
    -- resolve the customer's SCD2 version as of the transaction, not today
    left join {{ ref('dim_customer_scd') }} customer_dim
        on customer_dim.customer_id = txn.customer_id
       and txn.transaction_ts >= customer_dim.valid_from
       and txn.transaction_ts < customer_dim.valid_to
)

select
    transaction_id,
    coalesce(transaction_date_key, -1) as transaction_date_key,
    coalesce(transaction_time_key, -1) as transaction_time_key,
    store_key,

    -- -2 (Not Applicable): no loyalty scan on this basket.
    -- -1 (Unknown): a customer_id was scanned but no SCD2 version resolved
    --   (data-quality gap, not a business "no loyalty" case) — measured
    --   below via customer_lookup_unresolved so it's never silently hidden.
    coalesce(
        customer_key,
        case when customer_id is null then '-2' else '-1' end
    ) as customer_key,

    net_sales_amount,
    item_count,
    1 as transaction_count,
    case when customer_id is not null then 1 else 0 end as loyalty_identified_transaction_count,
    (customer_id is not null and customer_key is null) as customer_lookup_unresolved,
    batch_id
from matched
