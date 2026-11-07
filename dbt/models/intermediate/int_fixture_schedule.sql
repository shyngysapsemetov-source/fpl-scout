-- Grain: one row per fixture, past seasons and this one, played or not. The rating model rates
-- every fixture as of `rated_at`, the first kickoff of its gameweek (the deadline view, D103):
-- no fixture's rating sees results from its own gameweek. Upcoming fixtures get ratings too.
{%- set this_season = var('season') | replace('_', '-') %}

with all_fixtures as (
select * from {{ ref('stg_history__fixture_schedule') }}
union all by name
select
    '{{ this_season }}'                 as season,
    f.fixture_id,
    f.kickoff_at,
    f.gameweek,
    h.team_code                         as home_code,
    a.team_code                         as away_code,
    f.is_finished
from {{ ref('stg_fpl__fixtures') }} f
join {{ ref('stg_fpl__teams') }} h on h.team_id = f.home_team_id
join {{ ref('stg_fpl__teams') }} a on a.team_id = f.away_team_id
where f.kickoff_at is not null
)

select
    *,
    coalesce(min(kickoff_at) over (partition by season, gameweek), kickoff_at) as rated_at
from all_fixtures
