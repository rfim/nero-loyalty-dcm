-- Grain: date, store, reward_id. Exposes only known attributes (reward_id
-- itself) — no invented reward names, categories or point values, since no
-- reward master source exists.
select
    d.full_date as redemption_date,
    s.store_id,
    s.store_name,
    f.reward_id,
    count(*) as redemption_count
from {{ ref('fact_loyalty_event') }} f
join {{ ref('dim_date') }} d on d.date_key = f.event_date_key
join {{ ref('dim_store') }} s on s.store_key = f.store_key
where f.event_type = 'redeem'
group by 1, 2, 3, 4
