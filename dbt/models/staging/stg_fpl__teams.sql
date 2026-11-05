with latest as (
    select payload
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

unnested as (
    select unnest(cast(payload->'teams' as json[])) as t
    from latest
)

select
    (t->>'id')::int                                         as team_id,
    (t->>'name')::varchar                                   as team_name,
    (t->>'short_name')::varchar                             as team_short_name,
    (t->>'strength')::int                                   as strength_overall,
    (t->>'strength_attack_home')::int                       as strength_attack_home,
    (t->>'strength_attack_away')::int                       as strength_attack_away,
    (t->>'strength_defence_home')::int                      as strength_defence_home,
    (t->>'strength_defence_away')::int                      as strength_defence_away
from unnested
