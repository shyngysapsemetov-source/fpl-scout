-- Grain: one row per fixture, past seasons and this one, played or not. The rating model rates
-- every fixture as of just before its kickoff, so upcoming fixtures get ratings too.
{%- set this_season = var('season') | replace('_', '-') %}

select * from {{ ref('stg_history__fixture_schedule') }}
union all by name
select
    '{{ this_season }}'                 as season,
    f.fixture_id,
    f.kickoff_at,
    h.team_code                         as home_code,
    a.team_code                         as away_code,
    f.is_finished
from {{ ref('stg_fpl__fixtures') }} f
join {{ ref('stg_fpl__teams') }} h on h.team_id = f.home_team_id
join {{ ref('stg_fpl__teams') }} a on a.team_id = f.away_team_id
where f.kickoff_at is not null
