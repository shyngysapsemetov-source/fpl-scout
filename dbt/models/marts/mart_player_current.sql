-- Grain: one row per player (D35). Each player's latest rolling values, current price and
-- status, and ranks within position.
-- Rank eligibility (D13, D33): season minutes >= 40% of the minutes his team played while he
-- was registered there, judged once on the season and applied to every ranked window.
-- Ranked (D31, D32): points per match and each category's primary predictor, for season and
-- last 5. Ties share a rank. A metric is ranked only for the positions it scores for.
{%- set ranked = [
    ('points_per_match',           'desc', none),
    ('minutes_per_match',          'desc', none),
    ('xg_per90',                   'desc', none),
    ('xa_per90',                   'desc', none),
    ('xgc_per90',                  'asc',  ['GKP', 'DEF']),
    ('saves_per90',                'desc', ['GKP']),
    ('defensive_contribution_per90', 'desc', ['DEF', 'MID', 'FWD']),
    ('bps_per90',                  'desc', none),
] %}

with latest as (
    select *
    from {{ ref('fct_player_rolling') }}
    qualify row_number() over (partition by player_id order by kickoff_at desc, fixture_id desc) = 1
),

snapshot as (
    select s.*, p.position_code
    from {{ ref('stg_fpl__player_snapshots') }} s
    join {{ ref('stg_fpl__positions') }} p using (position_id)
    qualify row_number() over (partition by s.player_id order by s.snapshot_date desc) = 1
),

base as (
    select
        s.player_id,
        s.web_name,
        s.team_id                                       as current_team_id,
        t.team_short_name                               as current_team,
        s.position_code,
        s.price,
        s.selected_by_pct,
        s.status,
        s.chance_of_playing_next_pct,
        s.news,
        s.snapshot_date                                 as as_of_date,
        l.gameweek                                      as last_gameweek,
        coalesce(l.minutes_played_season, 0)
            >= 0.4 * 90 * l.team_fixtures_season        as is_rank_eligible,
        l.* exclude (player_id, player_fixture_id, fixture_id, gameweek, kickoff_at, team_id,
                     is_played, minutes, total_points)
    from snapshot s
    join {{ ref('stg_fpl__teams') }} t on t.team_id = s.team_id
    left join latest l using (player_id)
)

select
    *,
    {%- for w in ['season', 'l5'] %}
    {%- set outer = loop %}
    {%- for metric, direction, positions in ranked %}
    {%- set col = metric ~ '_' ~ w %}
    {%- set applies = "position_code in ('" ~ positions | join("', '") ~ "')" if positions else 'true' %}
    case when is_rank_eligible and {{ applies }} then
        rank() over (partition by position_code, is_rank_eligible and {{ applies }}
                     order by {{ col }} {{ direction }} nulls last)
    end as {{ col }}_rank{{ "," if not (outer.last and loop.last) }}
    {%- endfor %}
    {%- endfor %}
from base
