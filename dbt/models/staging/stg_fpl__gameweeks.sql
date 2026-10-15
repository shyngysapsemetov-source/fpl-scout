with latest as (
    select events
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

unnested as (
    select unnest(events) as e
    from latest
)

select
    e.id                            as gameweek,
    e.name                          as gameweek_name,
    e.deadline_time::timestamptz    as deadline_at,
    e.finished                      as is_finished,
    e.data_checked                  as is_data_checked,   -- bonus and corrections final
    e.is_current,
    e.is_next,
    e.average_entry_score
from unnested
