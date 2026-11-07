{{ config(materialized="table") }}
-- Grain: one row per player per fixture, past seasons (archive) and this one, played and
-- upcoming (D84, D87). Upcoming rows have null stats; they exist so the same pre-match feature
-- logic produces forecasts for fixtures not yet played. player_key = season-player_id.
{%- set this_season = var('season') | replace('_', '-') %}

with current_played as (
    select
        '{{ this_season }}'                                   as season,
        '{{ this_season }}-' || f.player_id                   as player_key,
        f.player_id,
        m.player_code,
        m.web_name                                            as player_name,
        p.position_code,
        f.fixture_id,
        f.gameweek,
        f.kickoff_at,
        f.was_home,
        t.team_code,
        o.team_code                                           as opponent_code,
        f.minutes, f.starts, f.total_points, f.goals_scored, f.assists, f.clean_sheets,
        f.goals_conceded, f.own_goals, f.penalties_saved, f.penalties_missed, f.yellow_cards,
        f.red_cards, f.saves, f.bonus, f.bps, f.xg, f.xa, f.xgc, f.defensive_contribution,
        f.price,
        null::double                                          as fpl_xp,
        false                                                 as is_upcoming
    from {{ ref('fct_player_fixtures') }} f
    join {{ ref('int_player_fixture_points') }} p using (player_id, fixture_id)
    join {{ ref('mart_player_current') }} m using (player_id)
    join {{ ref('stg_fpl__teams') }} t on t.team_id = f.team_id
    join {{ ref('stg_fpl__teams') }} o on o.team_id = f.opponent_team_id
),

-- Upcoming fixtures for every current player at his current club.
current_upcoming as (
    select
        '{{ this_season }}'                                   as season,
        '{{ this_season }}-' || m.player_id                   as player_key,
        m.player_id,
        m.player_code,
        m.web_name                                            as player_name,
        m.position_code,
        n.fixture_id,
        n.gameweek,
        n.kickoff_at,
        n.is_home                                             as was_home,
        n.team_code,
        n.opponent_code,
        {%- for c in ['minutes', 'starts', 'total_points', 'goals_scored', 'assists', 'clean_sheets',
                      'goals_conceded', 'own_goals', 'penalties_saved', 'penalties_missed',
                      'yellow_cards', 'red_cards', 'saves', 'bonus', 'bps'] %}
        null::int                                             as {{ c }},
        {%- endfor %}
        null::double as xg, null::double as xa, null::double as xgc,
        null::int                                             as defensive_contribution,
        m.price,
        null::double                                          as fpl_xp,
        true                                                  as is_upcoming
    from {{ ref('mart_player_current') }} m
    join {{ ref('fct_team_next_fixtures') }} n on n.team_id = m.current_team_id
),

history as (
    select
        season, player_key, player_id, player_code, player_name, position_code, fixture_id, gameweek,
        kickoff_at, was_home, team_code, opponent_code, minutes, starts, total_points,
        goals_scored, assists, clean_sheets, goals_conceded, own_goals, penalties_saved,
        penalties_missed, yellow_cards, red_cards, saves, bonus, bps, xg, xa, xgc,
        defensive_contribution, price, fpl_xp, false as is_upcoming
    from {{ ref('stg_history__player_fixtures') }}
)

select * from history
union all by name select * from current_played
union all by name select * from current_upcoming
