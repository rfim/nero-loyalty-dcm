-- Fails if any customer has two SCD2 intervals that overlap in time.
select a.customer_id
from {{ ref('dim_customer_scd') }} a
join {{ ref('dim_customer_scd') }} b
  on a.customer_id = b.customer_id
 and a.customer_key <> b.customer_key
 and a.valid_from < b.valid_to
 and b.valid_from < a.valid_to
