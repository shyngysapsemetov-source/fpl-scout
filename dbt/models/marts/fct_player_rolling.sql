-- Grain: one row per player per fixture (D12, D35), with every window's value as of that match.
-- Form windows count matches played only (D11): a 0-minute row carries forward the values from
-- the player's last played match. Availability windows count every team fixture (D34).
-- Predictors are per 90, as sum / sum(minutes) * 90 (D29, D30); points are per match played.
{%- set windows = {'l5': 'rows between 4 preceding and current row',
                   'l10': 'rows between 9 preceding and current row',
                   'season': 'rows between unbounded preceding and current row'} %}
{%- set categories = ['appearance', 'goal', 'assist', 'clean_sheet', 'goals_conceded', 'save',
                      'penalty_save', 'penalty_miss', 'yellow_card', 'red_card', 'own_goal',
                      'bonus', 'defensive_contribution'] %}
{#- Primary and secondary predictors from the D26 table, as per-90 rates. #}
{%- set per90 = ['xg', 'xa', 'xgc', 'saves', 'defensive_contribution', 'bps',
                 'threat', 'creativity', 'influence', 'ict_index'] %}

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
        f.is_played, f.minutes, f.starts, f.total_points,
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
        fx.team_id, fx.is_played, fx.minutes, fx.total_points,
        a.* exclude (player_fixture_id),
        fm.* exclude (player_fixture_id)
    from fixtures fx
    join availability a using (player_fixture_id)
    left join form fm using (player_fixture_id)
)

-- Carry the last played match's form values onto 0-minute rows. Before a player's first
-- appearance they stay null.
select
    player_fixture_id, player_id, fixture_id, gameweek, kickoff_at, team_id, is_played,
    minutes, total_points,
    {%- for name, _ in avail %} {{ name }},{% endfor %}
    {%- for name, _ in form %}
    last_value({{ name }} ignore nulls) over carry as {{ name }}{{ "," if not loop.last }}
    {%- endfor %}
from joined
window carry as (partition by player_id order by kickoff_at, fixture_id
                 rows between unbounded preceding and current row)
