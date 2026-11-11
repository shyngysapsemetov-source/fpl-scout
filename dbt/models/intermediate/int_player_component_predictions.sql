{{ config(materialized="table") }}
-- Grain: one row per outfield player per fixture (int_player_component_features). Component
-- predictions from fitted coefficients in the component_glm seed (D119; fitted by
-- agent/fit_components_glm.py on training seasons only). Used by fct_player_expected_points
-- only when its switches are on (xpts_dc_model, xpts_ga_level_fix; both off by default).
--   DC (D118): per position, an NB2 count model: rate per 90 = exp(b0 + sum coef x (term - mu)
--     / sd), NULL terms -> the training median. P(count >= threshold) under the NB with mean
--     rate x minutes / 90 in each minutes scenario (87 / 62 / 22), mixed by P(60+), P(early
--     sub), P(sub); then the per-position Platt calibration logit P = a + b logit P_raw.
--   Level fix (D119): per position, actual / formula goals and assists on the training
--     seasons, a multiplier on the formula's xp_goals and xp_assists.
{%- set dc_terms = ['log_dc90_season', 'log_dc90_l5', 'log_dc90_l3', 'log_dc_minutes_season',
    'log_opp_dc_forced', 'log_team_dc_made', 'log_opp_dc_made', 'exp_goal_diff', 'is_home',
    'gap_x_home', 'log_team_xg_for_l5', 'log_opp_xg_for_l5', 'role_min_delta',
    'role_start_delta', 'rest_days', 'matches_l14'] %}
with seed as (
    select * from {{ ref('component_glm') }}
),

f as (
    select season, player_key, fixture_id, position_code, p_60, p_early, p_sub,
           {%- for t in dc_terms %}
           {{ t }}::double as {{ t }}{{ "," if not loop.last }}
           {%- endfor %}
    from {{ ref('int_player_component_features') }}
    where position_code in ('DEF', 'MID', 'FWD')
),

long as (
    select season, player_key, fixture_id, position_code, term, x
    from (select season, player_key, fixture_id, position_code, {{ dc_terms | join(', ') }} from f)
    unpivot include nulls (x for term in ({{ dc_terms | join(', ') }}))
),

linear as (
    select l.season, l.player_key, l.fixture_id,
           sum(s.coef * (coalesce(l.x, s.median) - s.mu) / s.sd) as xb
    from long l
    join seed s on s.component = 'dc' and s.position_code = l.position_code and s.term = l.term
    group by all
),

params as (
    select position_code,
           max(coef) filter (where component = 'dc' and term = '_intercept')     as b0,
           max(coef) filter (where component = 'dc' and term = '_alpha')         as nb_alpha,
           max(coef) filter (where component = 'dc' and term = '_cal_a')         as cal_a,
           max(coef) filter (where component = 'dc' and term = '_cal_b')         as cal_b,
           max(coef) filter (where component = 'goals' and term = '_level_scale')   as goals_level_scale,
           max(coef) filter (where component = 'assists' and term = '_level_scale') as assists_level_scale
    from seed
    group by all
),

thresholds as (
    select replace(season, '_', '-') as season, position_code, threshold as thr
    from {{ ref('scoring_thresholds') }}
    where rule = 'defensive_contribution_min'
),

rates as (
    select f.season, f.player_key, f.fixture_id, f.position_code, f.p_60, f.p_early, f.p_sub,
           exp(p.b0 + l.xb)                                                  as dc_rate,
           1 / p.nb_alpha                                                    as nb_n,
           coalesce(t.thr, case f.position_code when 'DEF' then 10 else 12 end) as thr,
           p.cal_a, p.cal_b, p.goals_level_scale, p.assists_level_scale
    from f
    join linear l using (season, player_key, fixture_id)
    join params p on p.position_code = f.position_code
    left join thresholds t on t.season = f.season and t.position_code = f.position_code
),

scenarios as (
    select *,
           greatest(dc_rate * 87 / 90, 1e-9) as mu60,
           greatest(dc_rate * 62 / 90, 1e-9) as mu_early,
           greatest(dc_rate * 22 / 90, 1e-9) as mu_sub
    from rates
),

hit as (
    select *,
           p_60 * {{ nb_sf('mu60', 'nb_n', 'thr') }} + p_early * {{ nb_sf('mu_early', 'nb_n', 'thr') }}
           + p_sub * {{ nb_sf('mu_sub', 'nb_n', 'thr') }}
                                                                             as dc_p_hit_raw
    from scenarios
)

select
    season, player_key, fixture_id, position_code, dc_rate, dc_p_hit_raw,
    1 / (1 + exp(-(cal_a + cal_b * ln(least(greatest(dc_p_hit_raw, 1e-6), 1 - 1e-6)
                                     / (1 - least(greatest(dc_p_hit_raw, 1e-6), 1 - 1e-6))))))
                                                                             as dc_p_hit,
    goals_level_scale, assists_level_scale
from hit
