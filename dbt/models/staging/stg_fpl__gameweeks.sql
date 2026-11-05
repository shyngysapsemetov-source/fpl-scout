with latest as (
    select payload
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

unnested as (
    select unnest(cast(payload->'events' as json[])) as e
    from latest
)

select
    (e->>'id')::int                                         as gameweek,
    (e->>'name')::varchar                                   as gameweek_name,
    (e->>'deadline_time')::timestamptz                      as deadline_at,
    (e->>'finished')::boolean                               as is_finished,
    (e->>'data_checked')::boolean                           as is_data_checked, -- bonus and corrections final
    (e->>'is_current')::boolean                             as is_current,
    (e->>'is_next')::boolean                                as is_next,
    (e->>'average_entry_score')::int                        as average_entry_score
from unnested
