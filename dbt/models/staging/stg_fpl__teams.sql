with latest as (
    select teams
    from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

unnested as (
    select unnest(teams) as t
    from latest
)

select
    t.id                    as team_id,
    t.name                  as team_name,
    t.short_name            as team_short_name,
    t.strength              as strength_overall,
    t.strength_attack_home,
    t.strength_attack_away,
    t.strength_defence_home,
    t.strength_defence_away
from unnested
