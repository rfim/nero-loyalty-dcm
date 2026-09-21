-- See bronze_stores.sql for the QUALIFY/append-only-staging rationale.
-- `not (event_type = 'redeem' and reward_id is null)` replaces
-- PROCESS_BATCH's required_if policy check -- same rule, plain SQL.
{{ config(cluster_by=['event_ts']) }}
select
    e.event_id,
    e.customer_id,
    e.event_ts,
    e.event_type,
    e.reward_id,
    e.store_id,
    e.batch_id,
    e.ingested_at
from {{ source('nero_staging', 'loyalty_events') }} e
where e.event_id is not null
  and e.customer_id is not null
  and e.event_ts is not null
  and e.event_type in ('signup', 'earn', 'redeem', 'tier_change')
  and not (e.event_type = 'redeem' and e.reward_id is null)
  and exists (select 1 from {{ ref('bronze_loyalty_customers') }} c where c.customer_id = e.customer_id)
  and (
    e.store_id is null
    or exists (select 1 from {{ ref('bronze_stores') }} s where s.store_id = e.store_id)
  )
qualify row_number() over (partition by e.event_id order by e.ingested_at desc) = 1
