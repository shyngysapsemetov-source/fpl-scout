{{ config(materialized="table") }}
-- Grain: one row per player per fixture, past seasons and this one, played and upcoming
-- (D42, D84-D87). Expected FPL points before kickoff, built category by category from the
-- D23 hierarchy, with every input taken before the fixture (int_player_prematch_features)
-- and the fixture's own expectations (fct_team_fixture_expectations).
--
-- Minutes (D85, D119: computed in int_player_minutes_model): P(start) = stabilised start rate (x the API's chance of playing, for the
-- current season's next GW only); P(60+ | start) = 1 - early-sub rate; P(sub appearance)
-- from the sub rate. Expected minutes: 87 for 60+, 62 for an early sub, 22 off the bench.
-- Attack: his stabilised per-90 xG and xA x expected minutes x a fixture factor = the
-- fixture's expected team goals / his team's average (opponent defence and venue).
-- Defence: clean sheet = P(60+) x P(0 conceded); goals conceded = E[floor(G/2)] under a
-- Poisson with the fixture's expected goals against: lambda/2 - (1 - exp(-2 lambda))/4.
-- Saves scale with the opponent's attack the same way. Bonus and cards from per-90 rates;
-- defensive contribution from his stabilised hit rate (2025/26 onward).
{%- set this_season = var('season') | replace('_', '-') %}
{#- D100 keeper rules; --vars '{"xpts_keeper_rules": false}' turns them off for A/B checks. #}
{%- set on = var('xpts_keeper_rules', true) %}
{%- set gk = "position_code = 'GKP'" if on else "false" %}
{%- set gk_f = "f.position_code = 'GKP'" if on else "false" %}
{%- set gk_m = "m.position_code = 'GKP'" if on else "false" %}
{#- D106 outfield minutes model; --vars '{"xpts_minutes_model": false}' turns it off. #}
{%- set mm = var('xpts_minutes_model', true) %}
{#- D108 per-position component scales; --vars '{"xpts_component_scales": false}' turns them off. #}
{%- set cs_on = var('xpts_component_scales', false) %}
{#- D109: weight on the breakout persistence term (0 = off). #}
{%- set bw = var('xpts_breakout_weight', 0) %}   {#- off by default: no pick gain (D111) #}
{#- D119 component models (int_player_component_predictions), off by default:
    xpts_dc_model      - outfield DC = 2 x the calibrated NB P(DC >= threshold);
    xpts_ga_level_fix  - outfield goals and assists x the per-position level scale. #}
{%- set dc_on = var('xpts_dc_model', false) %}
{%- set lvl_on = var('xpts_ga_level_fix', false) %}
{%- set rules_list = ['long_play', 'short_play', 'goals_scored', 'assists', 'clean_sheets',
    'goals_conceded', 'saves', 'yellow_cards', 'bonus', 'defensive_contribution'] %}

with f as (
    select * from {{ ref('int_player_prematch_features') }}
),

rules_all as (
    select replace(season, '_', '-') as season, position_code, rule, points
    from {{ ref('scoring_rules_history') }}
    union all
    select '{{ this_season }}', position_code, rule, points
    from {{ ref('stg_fpl__scoring_rules') }}
),

rules as (
    select
        season, position_code,
        {%- for r in rules_list %}
        max(points) filter (where rule = '{{ r }}') as pts_{{ r }}{{ "," if not loop.last }}
        {%- endfor %}
    from rules_all
    group by all
),

fixture as (
    select
        e.season, e.fixture_id, e.team_code,
        e.expected_goals_for, e.expected_goals_against, e.clean_sheet_prob,
        -- His team's average attack and the opponent's average attack, venue-neutral.
        e.attack * (r.mu_home + r.mu_away) / 2                   as team_base_xg,
        e.opponent_attack * (r.mu_home + r.mu_away) / 2          as opp_base_xg
    from {{ ref('fct_team_fixture_expectations') }} e
    join {{ ref('int_team_ratings') }} r using (season, fixture_id, team_code)
),

keeper as (
    -- D100: keepers are binary starters; their bonus and saves come from the fixture.
    select
        max(value) filter (where parameter = 'p_start_if_started_last') as p_if_started,
        max(value) filter (where parameter = 'p_start_if_not')          as p_if_not,
        max(value) filter (where parameter = 'bonus_intercept')         as bonus_a,
        max(value) filter (where parameter = 'bonus_cs_slope')          as bonus_b,
        max(value) filter (where parameter = 'saves_intercept')         as saves_a,
        max(value) filter (where parameter = 'saves_xga_slope')         as saves_b
    from {{ ref('keeper_params') }}
),

probs as (
    -- D119: the minutes model lives in int_player_minutes_model.
    select
        f.*,
        x.expected_goals_for, x.expected_goals_against, x.clean_sheet_prob,
        x.expected_goals_for / nullif(x.team_base_xg, 0)         as attack_factor,
        x.expected_goals_against / nullif(x.opp_base_xg, 0)      as save_factor,
        mm.p_start, mm.p_60, mm.p_early, mm.p_sub, mm.exp_minutes
    from f
    join fixture x using (season, fixture_id, team_code)
    join {{ ref('int_player_minutes_model') }} mm using (season, player_key, fixture_id)
),

minutes as (
    select * from probs
),

parts as (
    select
        m.*,
        2 * p_60 * r.pts_long_play / 2 + (p_early + p_sub) * r.pts_short_play as xp_appearance,
        xg_per90 * (87 * p_60 + 62 * p_early + 22 * p_sub) / 90 * coalesce(attack_factor, 1)
            * r.pts_goals_scored                                  as xp_goals,
        xa_per90 * (87 * p_60 + 62 * p_early + 22 * p_sub) / 90 * coalesce(attack_factor, 1)
            * r.pts_assists                                       as xp_assists,
        p_60 * clean_sheet_prob * r.pts_clean_sheets              as xp_clean_sheet,
        p_60 * (expected_goals_against / 2 - (1 - exp(-2 * expected_goals_against)) / 4)
            * r.pts_goals_conceded                                as xp_goals_conceded,
        case when {{ gk_m }}
             then greatest(0, k.saves_a + k.saves_b * expected_goals_against) * p_60 * r.pts_saves
             else saves_per90 * (87 * p_60 + 62 * p_early + 22 * p_sub) / 90 * coalesce(save_factor, 1)
                  / 3 * r.pts_saves end                           as xp_saves,
        case when {{ gk_m }}
             then greatest(0, k.bonus_a + k.bonus_b * clean_sheet_prob) * p_60 * r.pts_bonus
             else bonus_per90 * (87 * p_60 + 62 * p_early + 22 * p_sub) / 90 * r.pts_bonus end
                                                                  as xp_bonus,
        yellow_cards_per90 * (87 * p_60 + 62 * p_early + 22 * p_sub) / 90
            * r.pts_yellow_cards                                  as xp_cards,
        dc_hit_rate * (p_60 + 0.5 * p_early) * r.pts_defensive_contribution as xp_defensive_contribution,
        r.pts_defensive_contribution                              as pts_dc_rule
    from minutes m
    join rules r using (season, position_code)
    cross join keeper k
),

scaled as (
    select
        p.* {%- if cs_on %} replace (
        xp_goals * coalesce(sg.a, 1)                                          as xp_goals,
        xp_assists * coalesce(sa.a, 1)                                        as xp_assists,
        xp_defensive_contribution * coalesce(sd.a, 1)                         as xp_defensive_contribution,
        -- outfield bonus from the fixture: a + b * CS prob + c * expected attacking points per play
        case when sb.a is null then xp_bonus
             else greatest(0, sb.a + sb.b * clean_sheet_prob
                  + sb.c * (xp_goals + xp_assists) / nullif(p_60 + p_early + p_sub, 0))
                  * (p_60 + p_early + p_sub) end                              as xp_bonus)
        {%- endif %}
    from parts p
    {%- if cs_on %}
    left join {{ ref('component_scales') }} sg on sg.position_code = p.position_code and sg.component = 'goals'
    left join {{ ref('component_scales') }} sa on sa.position_code = p.position_code and sa.component = 'assists'
    left join {{ ref('component_scales') }} sd on sd.position_code = p.position_code and sd.component = 'defensive_contribution'
    left join {{ ref('component_scales') }} sb on sb.position_code = p.position_code and sb.component = 'bonus'
    {%- endif %}
),

-- D119: swap in the component models where switched on.
modelled as (
    select s.*
    {%- set swaps = [] %}
    {%- if lvl_on %}
    {%- do swaps.append("s.xp_goals * coalesce(cp.goals_level_scale, 1) as xp_goals") %}
    {%- do swaps.append("s.xp_assists * coalesce(cp.assists_level_scale, 1) as xp_assists") %}
    {%- endif %}
    {%- if dc_on %}
    {%- do swaps.append("case when cp.dc_p_hit is not null then cp.dc_p_hit * s.pts_dc_rule else s.xp_defensive_contribution end as xp_defensive_contribution") %}
    {%- endif %}
    {%- if swaps %} replace ({{ swaps | join(', ') }}) {%- endif %}
    from scaled s
    left join {{ ref('int_player_component_predictions') }} cp using (season, player_key, fixture_id)
),

breakout_asof as (
    -- His latest breakout state from an EARLIER gameweek (pre-deadline, D103).
    select s.player_key, s.fixture_id,
           coalesce(case when b.is_breakout then b.share_of_jump_kept_next5
                         * (b.recent_points_per_fixture - b.baseline_points_per_fixture) end, 0)
               * (s.p_60 + s.p_early + s.p_sub) * {{ bw }}            as xp_breakout,
           case when b.is_breakout then b.breakout_reason end           as breakout_reason
    from modelled s
    asof left join {{ ref('fct_player_breakouts') }} b
      on b.player_key = s.player_key and s.gameweek > b.gameweek
),

final as (
    select s.*, ba.xp_breakout, ba.breakout_reason
    from modelled s join breakout_asof ba using (player_key, fixture_id)
)

select
    p.season, player_key, player_id, player_name, p.position_code, team_code, opponent_code,
    fixture_id, gameweek, kickoff_at, was_home, is_upcoming, price,
    p_start, p_60, p_early, p_sub, exp_minutes, clean_sheet_prob as clean_sheet_prob_used,
    xp_appearance, xp_goals, xp_assists, xp_clean_sheet, xp_goals_conceded, xp_saves,
    xp_bonus, xp_cards, xp_defensive_contribution, xp_breakout, breakout_reason,
    xp_appearance + xp_goals + xp_assists + xp_clean_sheet + xp_goals_conceded + xp_saves
        + xp_bonus + xp_cards + xp_defensive_contribution + xp_breakout as xpts,
    -- D93, D94: calibrated level and spread, by season phase and position; ranking within a
    -- position is unchanged, and cross-position comparisons become fair.
    greatest(0, c.intercept + c.slope * (xp_appearance + xp_goals + xp_assists + xp_clean_sheet
        + xp_goals_conceded + xp_saves + xp_bonus + xp_cards + xp_defensive_contribution
        + xp_breakout))
                                                                   as xpts_calibrated,
    ppm_stable * (p_60 + p_early + p_sub)                          as naive_xpts,
    total_points                                                   as actual_points,
    minutes                                                        as actual_minutes,
    fpl_xp
from final p
left join {{ ref('xpts_calibration') }} c
       on p.gameweek between c.gw_from and c.gw_to and c.position_code = p.position_code
