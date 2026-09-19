-- See bronze_stores.sql for the QUALIFY/append-only-staging rationale.
-- home_store_id is nullable in the contract, so only checked against
-- bronze_stores when present (a null FK is valid, not a violation).
select
    c.customer_id,
    c.signup_date,
    c.tier,
    c.home_store_id,
    c.batch_id,
    c.ingested_at
from {{ source('nero_staging', 'loyalty_customers') }} c
where c.customer_id is not null
  and c.signup_date is not null
  and c.tier in ('Bronze', 'Silver', 'Gold')
  and (
    c.home_store_id is null
    or exists (select 1 from {{ ref('bronze_stores') }} s where s.store_id = c.home_store_id)
  )
qualify row_number() over (partition by c.customer_id order by c.ingested_at desc) = 1
