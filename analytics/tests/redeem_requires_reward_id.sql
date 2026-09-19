-- Contract policy: reward_id is required when event_type = 'redeem'.
-- bronze_loyalty_events already filters this out in SQL -- this test is
-- the regression safety net if that filter is ever changed or removed.
-- Returns violating rows; a dbt test passes when this returns zero rows.
select *
from {{ ref('bronze_loyalty_events') }}
where event_type = 'redeem'
  and reward_id is null
