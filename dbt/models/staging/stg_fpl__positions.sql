with latest as (
    select payload
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

unnested as (
    select unnest(cast(payload->'element_types' as json[])) as p
    from latest
)

select
    (p->>'id')::int                                         as position_id,
    (p->>'singular_name_short')::varchar                    as position_code, -- GKP, DEF, MID, FWD
    (p->>'singular_name')::varchar                          as position_name
from unnested
