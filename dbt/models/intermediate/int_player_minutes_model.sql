{{ config(materialized="table") }}
-- Grain: one row per player per fixture (int_player_prematch_features). The minutes model
-- (D85, D100, D106), moved out of fct_player_expected_points (D119) so the component features
-- can use it without reading a mart:
--   P(start): keepers are binary starters (keeper_params, by whether he started last); outfield
--     players with a previous fixture use the fitted minutes model (minutes_params); otherwise
--     the stabilised start rate. x the API's chance of playing for the current season's next
--     GW only.
--   P(60+) = P(start) x (1 - early-sub rate); P(early sub) = P(start) x early-sub rate;
--   P(sub appearance) = (1 - P(start)) x sub rate. Keepers: 0.98 / 0.02 / 0.
--   Expected minutes = 87 P(60+) + 62 P(early sub) + 22 P(sub).
{%- set this_season = var('season') | replace('_', '-') %}
{#- D100 keeper rules; --vars '{"xpts_keeper_rules": false}' turns them off for A/B checks. #}
{%- set on = var('xpts_keeper_rules', true) %}
{%- set gk = "position_code = 'GKP'" if on else "false" %}
{%- set gk_f = "f.position_code = 'GKP'" if on else "false" %}
{#- D106 outfield minutes model; --vars '{"xpts_minutes_model": false}' turns it off. #}
{%- set mm = var('xpts_minutes_model', true) %}

with f as (
    select * from {{ ref('int_player_prematch_features') }}
),

keeper as (
    select
        max(value) filter (where parameter = 'p_start_if_started_last') as p_if_started,
        max(value) filter (where parameter = 'p_start_if_not')          as p_if_not
    from {{ ref('keeper_params') }}
),

minutes_model as (
    select
        max(value) filter (where parameter = 'intercept')      as m_a,
        max(value) filter (where parameter = 'b_start_rate')   as m_rate,
        max(value) filter (where parameter = 'b_started_last') as m_last,
        max(value) filter (where parameter = 'b_last_minutes') as m_min,
        max(value) filter (where parameter = 'b_streak')       as m_streak
    from {{ ref('minutes_params') }}
),

availability as (
    -- The API's chance of playing applies to the very next gameweek only.
    select m.player_id, coalesce(m.chance_of_playing_next_pct, 100) / 100.0 as chance,
           (select min(gameweek) from {{ ref('fct_team_next_fixtures') }}) as next_gameweek
    from {{ ref('int_player_latest') }} m
),

probs as (
    select
        f.season, f.player_key, f.fixture_id, f.position_code, f.early_sub_rate, f.sub_rate,
        case when {{ gk_f }} and f.started_last is not null
             then case when f.started_last = 1 then k.p_if_started else k.p_if_not end
             {%- if mm %}
             when f.started_last is not null
             then least(0.99, greatest(0.01, mm.m_a + mm.m_rate * f.start_rate
                  + mm.m_last * f.started_last + mm.m_min * f.last_minutes / 90
                  + mm.m_streak * least(f.start_streak, 5) / 5))
             {%- endif %}
             else f.start_rate end
            * case when f.season = '{{ this_season }}' and f.is_upcoming
                             and f.gameweek = a.next_gameweek then a.chance else 1 end
                                                                  as p_start
    from f
    left join availability a on f.season = '{{ this_season }}' and a.player_id = f.player_id
    cross join keeper k
    cross join minutes_model mm
),

minutes as (
    select
        *,
        p_start * case when {{ gk }} then 0.98 else 1 - early_sub_rate end as p_60,
        p_start * case when {{ gk }} then 0.02 else early_sub_rate end     as p_early,
        case when {{ gk }} then 0 else (1 - p_start) * sub_rate end         as p_sub
    from probs
)

select
    season, player_key, fixture_id, p_start, p_60, p_early, p_sub,
    87 * p_60 + 62 * p_early + 22 * p_sub                                 as exp_minutes
from minutes
