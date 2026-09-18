-- Bronze -> silver, replacing PROCESS_BATCH's Python validation with plain
-- SQL filtering: only rows meeting every contract rule reach this table.
-- Bronze is append-only across every historical batch (each full-mode
-- landing re-inserts all current rows under a new batch_id), so QUALIFY
-- picks the latest landing per primary key -- the same "current state"
-- semantics PROCESS_BATCH's DELETE+reload used to guarantee.
select
    store_id,
    store_name,
    region,
    format,
    opened_date,
    batch_id,
    ingested_at
from {{ source('nero_bronze', 'stores') }}
where store_id is not null
  and store_name is not null and length(store_name) <= 200
  and region is not null and length(region) <= 100
  and format in ('High street', 'Drive-thru', 'Travel')
  and opened_date is not null
qualify row_number() over (partition by store_id order by ingested_at desc) = 1
