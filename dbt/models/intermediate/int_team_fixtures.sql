-- Grain: one row per team per finished fixture, past seasons and this one (D41, D49).
-- Team xG is the sum of its players' xG. Season keys use the archive's form, e.g. 2026-27.
{%- set this_season = var('season') | replace('_', '-') %}

with current_xg as (
    select fixture_id, team_id, sum(xg) as xg
    from {{ ref('fct_player_fixtures') }}
    group by all
),

fixtures as (
    select * from {{ ref('stg_fpl__fixtures') }} where is_finished
),

teams as (
    select team_id, team_code from {{ ref('stg_fpl__teams') }}
),

current_season as (
    select
        '{{ this_season }}'             as season,
        f.fixture_id,
        f.kickoff_at,
        t.team_code,
        o.team_code                     as opponent_code,
        side.is_home,
        xf.xg                           as xg_for,
        xa.xg                           as xg_against,
        case when side.is_home then f.home_score else f.away_score end as goals_for,
        case when side.is_home then f.away_score else f.home_score end as goals_against
    from fixtures f
    cross join (values (true), (false)) side(is_home)
    join teams t on t.team_id = case when side.is_home then f.home_team_id else f.away_team_id end
    join teams o on o.team_id = case when side.is_home then f.away_team_id else f.home_team_id end
    join current_xg xf on xf.fixture_id = f.fixture_id and xf.team_id = t.team_id
    join current_xg xa on xa.fixture_id = f.fixture_id and xa.team_id = o.team_id
)

select * from {{ ref('stg_history__team_fixtures') }}
union all by name
select * from current_season
