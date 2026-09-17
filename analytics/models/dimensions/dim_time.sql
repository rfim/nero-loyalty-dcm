with minutes as (
    {{ dbt_utils.generate_series(1440) }}
)

select
    (generated_number - 1) as minute_of_day,
    floor((generated_number - 1) / 60)::int * 100 + mod(generated_number - 1, 60) as time_key,
    floor((generated_number - 1) / 60)::int as hour_24,
    mod(generated_number - 1, 60) as minute,
    case when floor((generated_number - 1) / 60)::int < 12 then 'AM' else 'PM' end as period
from minutes
