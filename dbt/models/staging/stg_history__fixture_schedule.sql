-- Every fixture of each past season, with FPL team codes. Feeds the rating model's schedule.
with teams as (
    select season, id::int as team_id, code::int as team_code
    from {{ source('fpl_raw', 'history_teams') }}
)

select
    f.season,
    f.id::int                           as fixture_id,
    f.kickoff_time::timestamptz         as kickoff_at,
    nullif(f.event, '')::int            as gameweek,
    h.team_code                         as home_code,
    a.team_code                         as away_code,
    f.finished = 'True'                 as is_finished
from {{ source('fpl_raw', 'history_fixtures') }} f
join teams h on h.season = f.season and h.team_id = f.team_h::int
join teams a on a.season = f.season and a.team_id = f.team_a::int
