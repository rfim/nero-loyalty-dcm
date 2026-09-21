-- Grain: one row per event_id (degenerate dimension: event_type is carried
-- as a plain column, not modeled as its own dimension — only 4 values).
{{ config(cluster_by=['event_date_key']) }}
with evt as (
    select * from {{ ref('silver_loyalty_events') }}
),

matched as (
    select
        evt.event_id,
        evt.event_type,
        evt.batch_id,
        evt.reward_id,

        date_dim.date_key as event_date_key,
        coalesce(store_dim.store_key, '-2') as store_key,
        reward_dim.reward_key,

        coalesce(
            customer_dim.customer_key,
            '-1'
        ) as customer_key

    from evt
    left join {{ ref('dim_date') }} date_dim
        on date_dim.full_date = evt.event_date
    left join {{ ref('dim_store') }} store_dim
        on store_dim.store_id = evt.store_id
    -- dim_reward is a shell (no reward master) — every reward_id present
    -- here is honestly "Unknown" (-1), not enriched; a null reward_id
    -- (non-redeem events) is "Not Applicable" (-2).
    left join {{ ref('dim_reward') }} reward_dim
        on reward_dim.reward_id = evt.reward_id
    -- customer_id is required (not null) on loyalty_events per contract
    left join {{ ref('dim_customer_scd') }} customer_dim
        on customer_dim.customer_id = evt.customer_id
       and evt.event_ts >= customer_dim.valid_from
       and evt.event_ts < customer_dim.valid_to
)

select
    event_id,
    coalesce(event_date_key, -1) as event_date_key,
    customer_key,
    store_key,
    coalesce(
        reward_key,
        case when reward_id is null then '-2' else '-1' end
    ) as reward_key,
    -- raw reward_id carried through as a degenerate attribute: dim_reward
    -- never actually resolves a real ID (it's a shell, no reward master),
    -- so reporting "by reward" has to group on this, not on reward_key.
    reward_id,
    event_type,
    1 as event_count,
    case when event_type = 'signup' then 1 else 0 end as signup_count,
    case when event_type = 'earn' then 1 else 0 end as earn_count,
    case when event_type = 'redeem' then 1 else 0 end as redeem_count,
    case when event_type = 'tier_change' then 1 else 0 end as tier_change_count,
    batch_id
from matched
