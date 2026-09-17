with spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2020-01-01' as date)",
        end_date="cast('2031-01-01' as date)"
    ) }}
)

select
    cast(to_char(date_day, 'YYYYMMDD') as integer) as date_key,
    date_day as full_date,
    year(date_day) as year,
    quarter(date_day) as quarter,
    month(date_day) as month,
    monthname(date_day) as month_name,
    day(date_day) as day_of_month,
    dayofweekiso(date_day) as day_of_week,
    dayname(date_day) as day_name,
    weekofyear(date_day) as week_of_year,
    dayofweekiso(date_day) in (6, 7) as is_weekend
from spine
