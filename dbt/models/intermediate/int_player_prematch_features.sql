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
        coalesce(sum(total_points) filter (where played) over prior, 0)   as points_prior
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

-- The prior each rate shrinks towards, and its weight: his own last season if available
-- (D91), else the position (D57).
priors as (
    select
        *,
        case when has_prev then (prev_starts + {{ k }} * pos_start_rate) / (prev_fixtures + {{ k }})
             else pos_start_rate end                                                   as prior_start_rate,
        case when has_prev then (prev_early_subs + {{ k }} * pos_early_sub_rate) / (prev_starts + {{ k }})
             else pos_early_sub_rate end                                               as prior_early_sub_rate,
        case when has_prev then (prev_sub_apps + {{ k }} * pos_sub_rate)
                                / (prev_fixtures - prev_starts + {{ k }})
             else pos_sub_rate end                                                     as prior_sub_rate,
        {%- for m in per90 %}
        case when has_prev then (prev_{{ m }} + {{ k_min }} * pos_{{ m }}_per90 / 90)
                                / (prev_minutes + {{ k_min }}) * 90
             else pos_{{ m }}_per90 end                                                as prior_{{ m }}_per90,
        {%- endfor %}
        case when has_prev then (prev_points + {{ k }} * pos_ppm) / (prev_matches + {{ k }})
             else pos_ppm end                                                          as prior_ppm,
        case when has_prev then {{ prev_w }} else {{ k_start }} end                    as w_start,
        case when has_prev then {{ prev_w }} else {{ k }} end                          as w_match,
        case when has_prev then {{ prev_w }} * 90 else {{ k_min }} end                 as w_minutes
    from with_priors
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
    from priors
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
