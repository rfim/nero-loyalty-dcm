-- Fails if any real customer (excludes the -1/-2 standard members) has zero
-- or more than one row marked is_current.
select customer_id, count(*) as current_row_count
from {{ ref('dim_customer_scd') }}
where is_current
  and customer_id is not null
group by customer_id
having count(*) <> 1
