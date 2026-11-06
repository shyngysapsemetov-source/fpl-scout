-- Grain: one row per player per fixture (D12, D35), with every window's value as of that match.
-- Form windows count matches played only (D11): a 0-minute row carries forward the values from
-- the player's last played match. Availability windows count every team fixture (D34).
-- Predictors are per 90, as sum / sum(minutes) * 90 (D29, D30); points are per match played.
-- *_stable columns (D57, D58, D64) stabilise small samples: (rate * exposure + K * prior) /
--   (exposure + K), with K = 3 matches or fixtures, or 270 minutes for per-90 rates. Season's
--   prior is the position's season-to-date value as of kickoff; last 5 and last 10 are his
--   stabilised season plus the damped deviation of the window from it (D72).
{%- set windows = {'l5': 'rows between 4 preceding and current row',
                   'l10': 'rows between 9 preceding and current row',
                   'season': 'rows between unbounded preceding and current row'} %}
{%- set categories = ['appearance', 'goal', 'assist', 'clean_sheet', 'goals_conceded', 'save',
                      'penalty_save', 'penalty_miss', 'yellow_card', 'red_card', 'own_goal',
                      'bonus', 'defensive_contribution'] %}
{#- Primary and secondary predictors from the D26 table, as per-90 rates. #}
{%- set per90 = ['xg', 'xa', 'xgc', 'saves', 'defensive_contribution', 'bps',
                 'threat', 'creativity', 'influence', 'ict_index'] %}

{%- set k_matches = 3 %}
{%- set k_minutes = 270 %}
{#- Stabilised metrics (D64): (name, exposure column prefix, prior weight in that exposure). #}
{%- set stable = [('points_per_match', 'matches_played', k_matches),
                  ('minutes_per_match', 'matches_played', k_matches),
                  ('start_share', 'matches_played', k_matches),
                  ('played_share', 'team_fixtures', k_matches),
                  ('avg_minutes', 'team_fixtures', k_matches)] %}
{%- for m in per90 %}{% do stable.append((m ~ '_per90', 'minutes_played', k_minutes)) %}{% endfor %}
{#- Form columns: name -> expression over window w. #}
{%- set form = [] %}
{%- for w in windows %}
    {%- do form.append(('matches_played_' ~ w, 'count(*) over ' ~ w)) %}
    {%- do form.append(('minutes_played_' ~ w, 'sum(minutes) over ' ~ w)) %}
    {%- do form.append(('total_points_' ~ w, 'sum(total_points) over ' ~ w)) %}
    {%- do form.append(('points_per_match_' ~ w, 'sum(total_points) over ' ~ w ~ ' / count(*) over ' ~ w)) %}
    {%- do form.append(('minutes_per_match_' ~ w, 'sum(minutes) over ' ~ w ~ ' / count(*) over ' ~ w)) %}
    {%- do form.append(('start_share_' ~ w, 'sum(starts) over ' ~ w ~ ' / count(*) over ' ~ w)) %}
    {%- for c in categories %}
        {%- do form.append((c ~ '_points_per_match_' ~ w,
                            'sum(' ~ c ~ '_points) over ' ~ w ~ ' / count(*) over ' ~ w)) %}
    {%- endfor %}
    {%- for m in per90 %}
        {%- do form.append((m ~ '_per90_' ~ w,
                            'sum(' ~ m ~ ') over ' ~ w ~ ' / nullif(sum(minutes) over ' ~ w ~ ', 0) * 90')) %}
    {%- endfor %}
{%- endfor %}

{#- Availability columns over every team fixture (D34). #}
{%- set avail = [] %}
{%- for w in windows %}
    {%- do avail.append(('team_fixtures_' ~ w, 'count(*) over ' ~ w)) %}
    {%- do avail.append(('played_share_' ~ w, 'avg(is_played::int) over ' ~ w)) %}
    {%- do avail.append(('avg_minutes_' ~ w, 'avg(minutes) over ' ~ w)) %}
{%- endfor %}

{%- set window_clause %}
    window
    {%- for w, frame in windows.items() %}
        {{ w }} as (partition by player_id order by kickoff_at, fixture_id {{ frame }}){{ "," if not loop.last }}
    {%- endfor %}
{%- endset %}

with fixtures as (
    select
        f.player_fixture_id, f.player_id, f.fixture_id, f.gameweek, f.kickoff_at, f.team_id,
        f.is_played, f.minutes, f.starts, f.total_points, p.position_code,
        {%- for m in per90 %} f.{{ m }},{% endfor %}
        {%- for c in categories %}
        p.{{ c }}_points{{ "," if not loop.last }}
        {%- endfor %}
    from {{ ref('fct_player_fixtures') }} f
    join {{ ref('int_player_fixture_points') }} p using (player_id, fixture_id)
),

form as (
    select
        player_fixture_id,
        {%- for name, expr in form %}
        {{ expr }} as {{ name }}{{ "," if not loop.last }}
        {%- endfor %}
    from fixtures
    where is_played
    {{ window_clause }}
),

-- Position-wide priors as of each kickoff (D57, D64): season-to-date values across every
-- player in the position, over the same exposure the player's own metric uses.
position_priors as (
    select
        position_code,
        kickoff_at,
        sum(points) over w / nullif(sum(matches) over w, 0)          as prior_points_per_match,
        sum(minutes) over w / nullif(sum(matches) over w, 0)         as prior_minutes_per_match,
        sum(starts) over w / nullif(sum(matches) over w, 0)          as prior_start_share,
        {%- for m in per90 %}
        sum({{ m }}) over w / nullif(sum(minutes) over w, 0) * 90    as prior_{{ m }}_per90,
        {%- endfor %}
        sum(matches) over w / sum(fixtures) over w                   as prior_played_share,
        sum(minutes) over w / sum(fixtures) over w                   as prior_avg_minutes
    from (
        select
            position_code, kickoff_at,
            sum(total_points) filter (where is_played)               as points,
            count(*) filter (where is_played)                        as matches,
            count(*)                                                 as fixtures,
            sum(minutes)                                             as minutes,
            sum(starts)                                              as starts,
            {%- for m in per90 %}
            sum({{ m }})                                             as {{ m }}{{ "," if not loop.last }}
            {%- endfor %}
        from fixtures
        group by all
    )
    window w as (partition by position_code order by kickoff_at
                 range between unbounded preceding and current row)
),

availability as (
    select
        player_fixture_id,
        {%- for name, expr in avail %}
        {{ expr }} as {{ name }}{{ "," if not loop.last }}
        {%- endfor %}
    from fixtures
    {{ window_clause }}
),

joined as (
    select
        fx.player_fixture_id, fx.player_id, fx.fixture_id, fx.gameweek, fx.kickoff_at,
        fx.team_id, fx.position_code, fx.is_played, fx.minutes, fx.total_points,
        a.* exclude (player_fixture_id),
        fm.* exclude (player_fixture_id)
    from fixtures fx
    join availability a using (player_fixture_id)
    left join form fm using (player_fixture_id)
),

-- Carry the last played match's form values onto 0-minute rows. Before a player's first
-- appearance they stay null.
carried as (
    select
        player_fixture_id, player_id, fixture_id, gameweek, kickoff_at, team_id, position_code,
        is_played, minutes, total_points,
        {%- for name, _ in avail %} {{ name }},{% endfor %}
        {%- for name, _ in form %}
        last_value({{ name }} ignore nulls) over carry as {{ name }}{{ "," if not loop.last }}
        {%- endfor %}
    from joined
    window carry as (partition by player_id order by kickoff_at, fixture_id
                     rows between unbounded preceding and current row)
),

-- Stabilised values (D57, D64, D72). Season: (rate * exposure + K * position prior) /
-- (exposure + K), with the exposure each metric is measured over (matches played, minutes, or
-- team fixtures). Last 5 and last 10: stabilised season + (raw window - raw season) *
-- exposure / (exposure + K), i.e. his level plus a damped recent deviation.
season_stable as (
    select
        c.*,
        {%- for name, exposure, k in stable %}
        (coalesce(c.{{ name }}_season, 0) * coalesce(c.{{ exposure }}_season, 0)
            + {{ k }} * pp.prior_{{ name }})
            / (coalesce(c.{{ exposure }}_season, 0) + {{ k }})      as {{ name }}_season_stable{{ "," if not loop.last }}
        {%- endfor %}
    from carried c
    join position_priors pp using (position_code, kickoff_at)
)

select
    *,
    {%- for w in ['l5', 'l10'] %}
    {%- set outer = loop %}
    {%- for name, exposure, k in stable %}
    -- D72: his stabilised season level plus his recent deviation from it, damped by the
    -- window's sample. When the window holds the same matches as the season, the deviation
    -- is 0 and the two agree exactly.
    {{ name }}_season_stable
        + coalesce({{ name }}_{{ w }} - {{ name }}_season, 0)
          * coalesce({{ exposure }}_{{ w }}, 0) / (coalesce({{ exposure }}_{{ w }}, 0) + {{ k }})
                                                                    as {{ name }}_{{ w }}_stable{{ "," if not (outer.last and loop.last) }}
    {%- endfor %}
    {%- endfor %}
from season_stable
