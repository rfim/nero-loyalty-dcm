-- Fails if any customer has zero or more than one row marked is_current.
select customer_id, count(*) as current_row_count
from {{ ref('dim_customer_scd') }}
where is_current
group by customer_id
having count(*) <> 1
