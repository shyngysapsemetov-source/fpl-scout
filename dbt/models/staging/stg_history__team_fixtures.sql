-- One row per team per finished fixture, for past seasons from the archive (D49). Team xG is
-- the sum of its players' xG in that fixture. The team comes from the fixture and was_home, not
-- the archive's team-name column, so name spellings can't break the join.
with fixtures as (
    select
        season,
        id::int                         as fixture_id,
        kickoff_time::timestamptz       as kickoff_at,
        team_h::int                     as home_team_id,
        team_a::int                     as away_team_id,
        team_h_score::int               as home_score,
        team_a_score::int               as away_score
    from {{ source('fpl_raw', 'history_fixtures') }}
    where finished = 'True'
),

teams as (
    select season, id::int as team_id, code::int as team_code
    from {{ source('fpl_raw', 'history_teams') }}
),

team_xg as (
    select
        season,
        fixture::int                    as fixture_id,
        was_home = 'True'               as is_home,
        sum(expected_goals::double)     as xg
    from {{ source('fpl_raw', 'history_player_gw') }}
    group by all
),

sides as (
    select season, fixture_id, kickoff_at, true as is_home, home_team_id as team_id,
           away_team_id as opponent_id, home_score as goals_for, away_score as goals_against
    from fixtures
    union all
    select season, fixture_id, kickoff_at, false, away_team_id, home_team_id, away_score, home_score
    from fixtures
)

select
    s.season,
    s.fixture_id,
    s.kickoff_at,
    t.team_code,
    o.team_code                         as opponent_code,
    s.is_home,
    xf.xg                               as xg_for,
    xa.xg                               as xg_against,
    s.goals_for,
    s.goals_against
from sides s
join teams t on t.season = s.season and t.team_id = s.team_id
join teams o on o.season = s.season and o.team_id = s.opponent_id
join team_xg xf on xf.season = s.season and xf.fixture_id = s.fixture_id and xf.is_home = s.is_home
join team_xg xa on xa.season = s.season and xa.fixture_id = s.fixture_id and xa.is_home <> s.is_home
