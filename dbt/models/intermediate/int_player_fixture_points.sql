-- Grain: one row per player per fixture. Splits total_points into its scoring categories (D23):
-- the count of each event times its points from the API (D25), with thresholds from the seed
-- (D27). points_reconstructed must equal total_points; a test enforces that.
{%- set point_rules = ['long_play', 'short_play', 'goals_scored', 'assists', 'clean_sheets',
    'goals_conceded', 'saves', 'penalties_saved', 'penalties_missed', 'yellow_cards',
    'red_cards', 'own_goals', 'bonus', 'defensive_contribution'] %}
{%- set threshold_rules = ['long_play_minutes', 'saves_per_point', 'goals_conceded_per_point',
    'defensive_contribution_min'] %}

with stats as (
    select * from {{ ref('stg_fpl__player_fixture_stats') }}
),

-- A player's position is fixed for the season, so the latest snapshot is enough.
player_position as (
    select s.player_id, p.position_code
    from {{ ref('stg_fpl__player_snapshots') }} s
    join {{ ref('stg_fpl__positions') }} p using (position_id)
    qualify row_number() over (partition by s.player_id order by s.snapshot_date desc) = 1
),

rules as (
    select
        position_code,
        {%- for r in point_rules %}
        max(points) filter (where rule = '{{ r }}') as pts_{{ r }}{{ "," if not loop.last }}
        {%- endfor %}
    from {{ ref('stg_fpl__scoring_rules') }}
    group by position_code
),

thresholds as (
    select
        position_code,
        {%- for r in threshold_rules %}
        max(threshold) filter (where rule = '{{ r }}') as {{ r }}{{ "," if not loop.last }}
        {%- endfor %}
    from {{ ref('scoring_thresholds') }}
    where season = '{{ var("season") }}'
    group by position_code
),

breakdown as (
    select
        s.player_id,
        s.fixture_id,
        pp.position_code,
        s.total_points,
        case when s.minutes >= t.long_play_minutes then r.pts_long_play
             when s.minutes > 0 then r.pts_short_play
             else 0 end                                               as appearance_points,
        s.goals_scored * r.pts_goals_scored                           as goal_points,
        s.assists * r.pts_assists                                     as assist_points,
        -- The API's clean_sheets is already 0 below the minutes threshold.
        s.clean_sheets * r.pts_clean_sheets                           as clean_sheet_points,
        coalesce(s.goals_conceded // t.goals_conceded_per_point, 0)
            * r.pts_goals_conceded                                    as goals_conceded_points,
        coalesce(s.saves // t.saves_per_point, 0) * r.pts_saves       as save_points,
        s.penalties_saved * r.pts_penalties_saved                     as penalty_save_points,
        s.penalties_missed * r.pts_penalties_missed                   as penalty_miss_points,
        s.yellow_cards * r.pts_yellow_cards                           as yellow_card_points,
        s.red_cards * r.pts_red_cards                                 as red_card_points,
        s.own_goals * r.pts_own_goals                                 as own_goal_points,
        s.bonus * r.pts_bonus                                         as bonus_points,
        case when s.defensive_contribution >= t.defensive_contribution_min
             then r.pts_defensive_contribution else 0 end             as defensive_contribution_points
    from stats s
    join player_position pp using (player_id)
    join rules r using (position_code)
    left join thresholds t using (position_code)
)

select
    *,
    appearance_points + goal_points + assist_points + clean_sheet_points
        + goals_conceded_points + save_points + penalty_save_points + penalty_miss_points
        + yellow_card_points + red_card_points + own_goal_points + bonus_points
        + defensive_contribution_points                               as points_reconstructed
from breakdown
