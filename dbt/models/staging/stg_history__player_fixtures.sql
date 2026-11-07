-- One row per player per fixture for past seasons, from the archive (D49, D84): the same
-- stats as stg_fpl__player_fixture_stats, cast from text. player_id is the archive's element
-- id, which is only unique within a season, so player_key = season-element is the stable key.
-- 2024/25 "AM" rows (that season's Assistant Manager chip) are not players and are dropped.
-- defensive_contribution exists only from 2025/26. The archive has exact duplicate rows for
-- one 2025/26 player (element 100, 10 fixtures), so rows are deduplicated on the grain.
with fixtures as (
    select season, id::int as fixture_id, team_h::int as home_team_id, team_a::int as away_team_id
    from {{ source('fpl_raw', 'history_fixtures') }}
),

teams as (
    select season, id::int as team_id, code::int as team_code
    from {{ source('fpl_raw', 'history_teams') }}
)

select
    g.season,
    g.season || '-' || g.element                              as player_key,
    g.element::int                                            as player_id,
    pr.code::int                                              as player_code,   -- stable across seasons
    g.name                                                    as player_name,
    case g.position when 'GK' then 'GKP' else g.position end  as position_code,
    g.fixture::int                                            as fixture_id,
    g.GW::int                                                 as gameweek,
    g.kickoff_time::timestamptz                               as kickoff_at,
    g.was_home = 'True'                                       as was_home,
    t.team_code,
    o.team_code                                               as opponent_code,
    g.minutes::int                                            as minutes,
    g.starts::int                                             as starts,
    g.total_points::int                                       as total_points,
    g.goals_scored::int                                       as goals_scored,
    g.assists::int                                            as assists,
    g.clean_sheets::int                                       as clean_sheets,
    g.goals_conceded::int                                     as goals_conceded,
    g.own_goals::int                                          as own_goals,
    g.penalties_saved::int                                    as penalties_saved,
    g.penalties_missed::int                                   as penalties_missed,
    g.yellow_cards::int                                       as yellow_cards,
    g.red_cards::int                                          as red_cards,
    g.saves::int                                              as saves,
    g.bonus::int                                              as bonus,
    g.bps::int                                                as bps,
    g.expected_goals::double                                  as xg,
    g.expected_assists::double                                as xa,
    g.expected_goals_conceded::double                         as xgc,
    nullif(g.defensive_contribution, '')::int                 as defensive_contribution,
    g.value::int / 10.0                                       as price,
    g.xP::double                                              as fpl_xp
from {{ source('fpl_raw', 'history_player_gw') }} g
join fixtures f on f.season = g.season and f.fixture_id = g.fixture::int
join teams t on t.season = g.season
            and t.team_id = case when g.was_home = 'True' then f.home_team_id else f.away_team_id end
join teams o on o.season = g.season
            and o.team_id = case when g.was_home = 'True' then f.away_team_id else f.home_team_id end
left join {{ source('fpl_raw', 'history_players_raw') }} pr
       on pr.season = g.season and pr.id::int = g.element::int
where g.position <> 'AM'
qualify row_number() over (partition by g.season, g.element, g.fixture order by g.kickoff_time) = 1
