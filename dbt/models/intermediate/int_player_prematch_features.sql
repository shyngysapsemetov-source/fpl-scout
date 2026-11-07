{{ config(materialized="table") }}
-- Grain: one row per player per fixture (int_player_fixtures_all). Every feature uses only
-- matches that kicked off BEFORE this fixture (no leakage, D86): a strict pre-match lag.
--
-- Rates are season-to-date and stabilised (D64, D84): (sum + K * prior) / (exposure + K).
-- The prior is the position's season-to-date rate as of kickoff (falling back to last season's
-- full-season position rate before a season has data) or, when xpts_prev_weight > 0 and the
-- player played last season, his own last-season rate (itself stabilised to his position),
-- weighted as xpts_prev_weight matches of evidence (D91).
-- With xpts_form_blend > 0, start rate and per-90 rates blend in his last 5 fixtures, as his
-- season level plus a damped deviation (the D72 rule): (1 - b) * season + b * last5.
-- Tuning parameters (D91), set with --vars; defaults are v0:
{%- set k = 3 %}
{%- set k_min = 270 %}
{%- set k_start = var('xpts_k_start', 3) %}
{%- set blend = var('xpts_form_blend', 0.0) %}
{%- set prev_w = var('xpts_prev_weight', 0) %}
{#- D93: fading previous-season prior. Weight W0 matches at a player's first fixture of the
    season, falling linearly to 0 after F fixtures; 0 turns it off. #}
{%- set prev_w0 = var('xpts_prev_w0', 0) %}
{%- set prev_fade = var('xpts_prev_fade', 10) %}
{%- set per90 = ['xg', 'xa', 'bonus', 'saves', 'yellow_cards'] %}

with rows as (
    select *, minutes is not null and minutes > 0 as played
    from {{ ref('int_player_fixtures_all') }}
),

-- His own counts and sums over earlier fixtures of the season, and over his last 5 fixtures.
-- count(minutes) skips upcoming rows, whose stats are null.
own as (
    select
        *,
        count(minutes) over prior                                         as fixtures_prior,
        count(*) filter (where played) over prior                         as matches_prior,
        coalesce(sum(starts) over prior, 0)                               as starts_prior,
        count(*) filter (where starts = 1 and minutes < 60) over prior    as early_subs_prior,
        count(*) filter (where played and starts = 0) over prior          as sub_apps_prior,
        coalesce(sum(minutes) over prior, 0)                              as minutes_prior,
        {%- for m in per90 %}
        coalesce(sum({{ m }}) over prior, 0)                              as {{ m }}_prior,
        coalesce(sum({{ m }}) over last5, 0)                              as {{ m }}_l5,
        {%- endfor %}
        count(minutes) over last5                                         as fixtures_l5,
        coalesce(sum(starts) over last5, 0)                               as starts_l5,
        coalesce(sum(minutes) over last5, 0)                              as minutes_l5,
        count(*) filter (where played and defensive_contribution is not null) over prior as dc_matches_prior,
        count(*) filter (where played and defensive_contribution >= case position_code
                          when 'DEF' then 10 else 12 end) over prior      as dc_hits_prior,
        coalesce(sum(total_points) filter (where played) over prior, 0)   as points_prior,
        -- D100: did he start his last fixture of the season? (null before his first)
        last_value(starts ignore nulls) over prior                        as started_last
    from rows
    window prior as (partition by player_key order by kickoff_at, fixture_id
                     rows between unbounded preceding and 1 preceding),
           last5 as (partition by player_key order by kickoff_at, fixture_id
                     rows between 5 preceding and 1 preceding)
),

-- Position totals per kickoff, then cumulative; the as-of join below takes the latest
-- kickoff strictly before each fixture.
pos_by_kickoff as (
    select
        season, position_code, kickoff_at,
        count(minutes)                                                    as fixtures,
        count(*) filter (where played)                                    as matches,
        sum(starts)                                                       as starts,
        count(*) filter (where starts = 1 and minutes < 60)               as early_subs,
        count(*) filter (where played and starts = 0)                     as sub_apps,
        sum(minutes)                                                      as minutes,
        {%- for m in per90 %}
        sum({{ m }})                                                      as {{ m }},
        {%- endfor %}
        count(*) filter (where played and defensive_contribution is not null) as dc_matches,
        count(*) filter (where played and defensive_contribution >= case position_code
                          when 'DEF' then 10 else 12 end)                 as dc_hits,
        sum(total_points) filter (where played)                           as points
    from rows
    where not is_upcoming
    group by all
),

pos_cum as (
    select
        season, position_code, kickoff_at,
        {%- for c in ['fixtures', 'matches', 'starts', 'early_subs', 'sub_apps', 'minutes',
                      'dc_matches', 'dc_hits', 'points'] + per90 %}
        sum({{ c }}) over w as {{ c }}{{ "," if not loop.last }}
        {%- endfor %}
    from pos_by_kickoff
    window w as (partition by season, position_code order by kickoff_at)
),

-- Full-season position rates: the stand-in prior before a season has any data.
pos_season as (
    select season, position_code,
        sum(starts) / nullif(sum(fixtures), 0)                            as start_rate,
        sum(early_subs) / nullif(sum(starts), 0)                          as early_sub_rate,
        sum(sub_apps) / nullif(sum(fixtures) - sum(starts), 0)            as sub_rate,
        {%- for m in per90 %}
        sum({{ m }}) / nullif(sum(minutes), 0) * 90                       as {{ m }}_per90,
        {%- endfor %}
        sum(dc_hits) / nullif(sum(dc_matches), 0)                         as dc_hit_rate,
        sum(points) / nullif(sum(matches), 0)                             as ppm
    from pos_by_kickoff
    group by all
),

-- His own full previous season (D91), linked by the stable player code.
prev_player as (
    select
        season, player_code,
        count(minutes)                                                    as fixtures,
        count(*) filter (where played)                                    as matches,
        sum(starts)                                                       as starts,
        count(*) filter (where starts = 1 and minutes < 60)               as early_subs,
        count(*) filter (where played and starts = 0)                     as sub_apps,
        sum(minutes)                                                      as minutes,
        {%- for m in per90 %}
        sum({{ m }})                                                      as {{ m }},
        {%- endfor %}
        sum(total_points) filter (where played)                           as points
    from rows
    where not is_upcoming and player_code is not null
    group by all
),

season_order as (
    select season, lag(season) over (order by season) as prev_season
    from (select distinct season from rows)
),

with_priors as (
    select
        o.*,
        -- Position priors as of kickoff, falling back to last season's full-season rate.
        coalesce(pc.starts / nullif(pc.fixtures, 0), ps.start_rate)                    as pos_start_rate,
        coalesce(pc.early_subs / nullif(pc.starts, 0), ps.early_sub_rate)              as pos_early_sub_rate,
        coalesce(pc.sub_apps / nullif(pc.fixtures - pc.starts, 0), ps.sub_rate)        as pos_sub_rate,
        {%- for m in per90 %}
        coalesce(pc.{{ m }} / nullif(pc.minutes, 0) * 90, ps.{{ m }}_per90)           as pos_{{ m }}_per90,
        {%- endfor %}
        coalesce(pc.dc_hits / nullif(pc.dc_matches, 0), ps.dc_hit_rate)                as prior_dc_hit_rate,
        coalesce(pc.points / nullif(pc.matches, 0), ps.ppm)                            as pos_ppm,
        {{ prev_w }} > 0 and pp.fixtures > 0                                            as has_prev,
        pp.fixtures as prev_fixtures, pp.matches as prev_matches, pp.starts as prev_starts,
        pp.early_subs as prev_early_subs, pp.sub_apps as prev_sub_apps,
        pp.minutes as prev_minutes, pp.points as prev_points
        {%- for m in per90 %}, pp.{{ m }} as prev_{{ m }}{% endfor %}
    from own o
    left join season_order so on so.season = o.season
    asof left join pos_cum pc
        on pc.season = o.season and pc.position_code = o.position_code
       and o.kickoff_at > pc.kickoff_at
    left join pos_season ps on ps.season = coalesce(so.prev_season, o.season)
                           and ps.position_code = o.position_code
    left join prev_player pp on pp.season = so.prev_season and pp.player_code = o.player_code
),

-- The prior each rate shrinks towards and its weight (D57, D91, D93): the position prior with
-- weight K, plus, when he played last season, his own last-season rate (stabilised to the
-- position) with a weight that fades over his first F fixtures (W0 = xpts_prev_w0). The old
-- fixed-weight switch (xpts_prev_weight) still works on its own.
priors as (
    select
        *,
        case when pp_has then {{ prev_w0 }} * greatest(0, 1 - fixtures_prior / {{ prev_fade }}) else 0 end
                                                                                        as w_prev,
        {%- for name, num, den, posrate in [
             ('start', 'prev_starts', 'prev_fixtures', 'pos_start_rate'),
             ('early_sub', 'prev_early_subs', 'prev_starts', 'pos_early_sub_rate'),
             ('sub', 'prev_sub_apps', 'prev_fixtures - prev_starts', 'pos_sub_rate'),
             ('ppm', 'prev_points', 'prev_matches', 'pos_ppm')] %}
        coalesce(({{ num }} + {{ k }} * {{ posrate }}) / ({{ den }} + {{ k }}), {{ posrate }})
                                                                                        as prev_{{ name }}_rate,
        {%- endfor %}
        {%- for m in per90 %}
        coalesce((prev_{{ m }} + {{ k_min }} * pos_{{ m }}_per90 / 90) / (prev_minutes + {{ k_min }}) * 90,
                 pos_{{ m }}_per90)                                                     as prev_{{ m }}_rate,
        {%- endfor %}
    from (select *, coalesce(prev_fixtures, 0) > 0 as pp_has from with_priors)
),

priors2 as (
    select
        *,
        -- Fixed-weight switch (D91) if set, else the fading mix (D93).
        case when has_prev then {{ prev_w }} else {{ k_start }} + w_prev end           as w_start,
        case when has_prev then {{ prev_w }} else {{ k }} + w_prev end                 as w_match,
        case when has_prev then {{ prev_w }} * 90 else {{ k_min }} + w_prev * 90 end   as w_minutes,
        case when has_prev then prev_start_rate
             else ({{ k_start }} * pos_start_rate + w_prev * prev_start_rate) / ({{ k_start }} + w_prev) end
                                                                                        as prior_start_rate,
        case when has_prev then prev_early_sub_rate
             else ({{ k }} * pos_early_sub_rate + w_prev * prev_early_sub_rate) / ({{ k }} + w_prev) end
                                                                                        as prior_early_sub_rate,
        case when has_prev then prev_sub_rate
             else ({{ k }} * pos_sub_rate + w_prev * prev_sub_rate) / ({{ k }} + w_prev) end
                                                                                        as prior_sub_rate,
        case when has_prev then prev_ppm_rate
             else ({{ k }} * pos_ppm + w_prev * prev_ppm_rate) / ({{ k }} + w_prev) end as prior_ppm,
        {%- for m in per90 %}
        case when has_prev then prev_{{ m }}_rate
             else ({{ k_min }} * pos_{{ m }}_per90 + w_prev * 90 * prev_{{ m }}_rate)
                  / ({{ k_min }} + w_prev * 90) end                                    as prior_{{ m }}_per90{{ "," if not loop.last }}
        {%- endfor %}
    from priors
),

season_rates as (
    select
        *,
        (starts_prior + w_start * prior_start_rate) / (fixtures_prior + w_start)       as start_rate_season,
        {%- for m in per90 %}
        ({{ m }}_prior + w_minutes * prior_{{ m }}_per90 / 90) / (minutes_prior + w_minutes) * 90
                                                                                       as {{ m }}_per90_season,
        {%- endfor %}
        (early_subs_prior + w_match * prior_early_sub_rate) / (starts_prior + w_match) as early_sub_rate,
        (sub_apps_prior + w_match * prior_sub_rate)
            / (fixtures_prior - starts_prior + w_match)                                as sub_rate,
        (dc_hits_prior + {{ k }} * coalesce(prior_dc_hit_rate, 0)) / (dc_matches_prior + {{ k }}) as dc_hit_rate,
        (points_prior + w_match * prior_ppm) / (matches_prior + w_match)               as ppm_stable
    from priors2
)

-- Blend in the last 5 fixtures as season level + damped deviation (D72), weight b (D91).
select
    *,
    (1 - {{ blend }}) * start_rate_season + {{ blend }} * (
        start_rate_season
        + coalesce(starts_l5 / nullif(fixtures_l5, 0) - starts_prior / nullif(fixtures_prior, 0), 0)
          * fixtures_l5 / (fixtures_l5 + {{ k }}))                                     as start_rate,
    {%- for m in per90 %}
    (1 - {{ blend }}) * {{ m }}_per90_season + {{ blend }} * (
        {{ m }}_per90_season
        + coalesce({{ m }}_l5 / nullif(minutes_l5, 0) * 90
                   - {{ m }}_prior / nullif(minutes_prior, 0) * 90, 0)
          * minutes_l5 / (minutes_l5 + {{ k_min }}))                                   as {{ m }}_per90{{ "," if not loop.last }}
    {%- endfor %}
from season_rates
