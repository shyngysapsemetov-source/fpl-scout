with latest as (
    select element_types
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

unnested as (
    select unnest(element_types) as p
    from latest
)

select
    p.id                    as position_id,
    p.singular_name_short   as position_code,   -- GKP, DEF, MID, FWD
    p.singular_name         as position_name
from unnested
