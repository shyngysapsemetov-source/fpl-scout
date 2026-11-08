-- Grain: one row per team per upcoming fixture of the current season, within the next
-- {{ var('fixture_horizon', 10) }} gameweeks (D115); gw_index 1 = the very next one. Next-5 views filter gw_index <= 5. The single source for every "next fixtures" view (card chips, fixture grid,
-- next-5 averages), so no consumer has to remember that fixture_id repeats across seasons.
{%- set this_season = var('season') | replace('_', '-') %}

with upcoming as (
    select * from {{ ref('stg_fpl__fixtures') }}
    where not is_finished and gameweek is not null
),

next_gws as (
    select gameweek, row_number() over (order by gameweek) as gw_index
    from (select distinct gameweek from upcoming)
    qualify gw_index <= {{ var('fixture_horizon', 10) }}
)

select
    e.team_code,
    t.team_id,
    t.team_short_name                   as team,
    f.gameweek,
    g.gw_index,
    e.fixture_id,
    e.kickoff_at,
    e.is_home,
    e.opponent_code,
    o.team_short_name                   as opponent,
    e.expected_goals_for,
    e.expected_goals_against,
    e.clean_sheet_prob
from {{ ref('fct_team_fixture_expectations') }} e
join upcoming f on f.fixture_id = e.fixture_id
join {{ ref('stg_fpl__teams') }} t on t.team_code = e.team_code
join {{ ref('stg_fpl__teams') }} o on o.team_code = e.opponent_code
join next_gws g on g.gameweek = f.gameweek
where e.season = '{{ this_season }}'
