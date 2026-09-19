-- Fails if fact_loyalty_event's row count drifts from staging.
with staging as (
    select count(*) as event_count from {{ ref('silver_loyalty_events') }}
),
fact as (
    select count(*) as event_count from {{ ref('fact_loyalty_event') }}
)
select staging.event_count, fact.event_count
from staging
join fact
    on staging.event_count != fact.event_count
