-- Grain: one row per player per fixture. Double gameweeks stay two rows.
-- The player's own team is not in the API's history rows, so it comes from the fixture:
-- that keeps it right for players who moved clubs mid-season.
with stats as (
    select * from {{ ref('stg_fpl__player_fixture_stats') }}
),

fixtures as (
    select * from {{ ref('stg_fpl__fixtures') }}
)

select
    s.player_id || '-' || s.fixture_id                                      as player_fixture_id,
    s.player_id,
    s.fixture_id,
    s.gameweek,
    s.kickoff_at,
    s.was_home,
    case when s.was_home then f.home_team_id else f.away_team_id end       as team_id,
    s.opponent_team_id,
    case when s.was_home then f.home_score else f.away_score end           as team_goals,
    case when s.was_home then f.away_score else f.home_score end           as opponent_goals,
    case when s.was_home then f.home_difficulty else f.away_difficulty end as fixture_difficulty,
    s.minutes > 0                                                          as is_played,
    s.* exclude (player_id, fixture_id, gameweek, kickoff_at, was_home, opponent_team_id)
from stats s
join fixtures f using (fixture_id)
