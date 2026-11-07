-- Grain: one row per player (D35). Each player's latest rolling values, current price and
-- status, and ranks within position.
-- Rank eligibility (D13, D33): season minutes >= 40% of the minutes his team played while he
-- was registered there, judged once on the season and applied to every ranked window.
-- Ranked (D31, D32): points per match and each category's primary predictor, for season and
-- last 5. Ties share a rank. A metric is ranked only for the positions it scores for.
-- Percentiles (D61, D71) for the scout card: 0-100 mid-rank within position among eligible
-- players, higher is always better (xGC is inverted).
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

{%- set pctl = [
    ('points_per_match_season',             'asc',  none),
    ('points_per_match_l5',                 'asc',  none),
    ('xg_per90_season',                     'asc',  none),
    ('xa_per90_season',                     'asc',  none),
    ('bps_per90_season',                    'asc',  none),
    ('minutes_per_match_season',            'asc',  none),
    ('played_share_l5',                     'asc',  none),
    ('xgc_per90_season',                    'desc', ['GKP', 'DEF']),
    ('next5_clean_sheet_prob',              'asc',  ['GKP', 'DEF']),
    ('defensive_contribution_per90_season', 'asc',  ['DEF', 'MID', 'FWD']),
    ('saves_per90_season',                  'asc',  ['GKP']),
    ('points_per_million',                  'asc',  none),
    ('xpts_next_gw',                        'asc',  none),
    ('xpts_next5',                          'asc',  none),
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

-- Each team's next 5 gameweeks: average expected goals and clean-sheet chance per fixture.
next5 as (
    select
        team_code,
        count(*)                                        as next5_fixtures,
        avg(expected_goals_for)                         as next5_expected_goals_for,
        avg(clean_sheet_prob)                           as next5_clean_sheet_prob
    from {{ ref('fct_team_next_fixtures') }}
    group by team_code
),

-- Minutes clarity (D81): how he gets his minutes, across team fixtures while registered.
minutes_profile as (
    select
        player_id,
        count(*) filter (where starts = 1)                       as starts_season,
        count(*) filter (where minutes > 0 and starts = 0)       as sub_appearances_season,
        count(*) filter (where starts = 1 and minutes < 60)      as early_subs_season,
        count(*) filter (where minutes = 0)                      as zero_minute_fixtures_season,
        sum(xg)                                                  as xg_season,
        sum(xa)                                                  as xa_season
    from {{ ref('fct_player_fixtures') }}
    group by player_id
),

-- His share of his team's xG in the matches he played (D81): how central he is to the attack.
xg_share as (
    select
        f.player_id,
        sum(f.xg) / nullif(sum(t.xg_for), 0)                     as xg_share_season,
        sum(f.xa) / nullif(sum(t.xg_for), 0)                     as xa_share_season
    from {{ ref('fct_player_fixtures') }} f
    join {{ ref('stg_fpl__teams') }} tm on tm.team_id = f.team_id
    join {{ ref('int_team_fixtures') }} t
      on t.season = '{{ var("season") | replace("_", "-") }}'
     and t.fixture_id = f.fixture_id and t.team_code = tm.team_code
    where f.minutes > 0
    group by f.player_id
),

-- The very next gameweek on its own (D81); a double gameweek sums expected goals and
-- combines clean-sheet chances.
next_gw as (
    select
        team_code,
        min(gameweek)                                            as next_gameweek,
        string_agg(opponent || case when is_home then ' (H)' else ' (A)' end, ' + '
                   order by kickoff_at)                          as next_gw_opponents,
        count(*)                                                 as next_gw_fixtures,
        sum(expected_goals_for)                                  as next_gw_expected_goals_for,
        sum(expected_goals_against)                              as next_gw_expected_goals_against,
        1 - product(1 - clean_sheet_prob)                        as next_gw_clean_sheet_prob
    from {{ ref('fct_team_next_fixtures') }}
    where gameweek = (select min(gameweek) from {{ ref('fct_team_next_fixtures') }})
    group by team_code
),

-- Expected points (D87, D92): the very next gameweek (double gameweeks summed) and the next 5.
xpts as (
    select
        player_id,
        sum(xpts) filter (where gameweek = (select min(gameweek) from {{ ref('fct_team_next_fixtures') }}))
                                                                 as xpts_next_gw,
        sum(xpts)                                                as xpts_next5,
        avg(p_start) filter (where gameweek = (select min(gameweek) from {{ ref('fct_team_next_fixtures') }}))
                                                                 as p_start_next_gw
    from {{ ref('fct_player_expected_points') }}
    where is_upcoming and season = '{{ var("season") | replace("_", "-") }}'
    group by player_id
),

base as (
    select
        s.player_id,
        s.player_code,
        s.web_name,
        s.team_id                                       as current_team_id,
        t.team_short_name                               as current_team,
        tc.primary_colour                               as team_primary_colour,
        tc.secondary_colour                             as team_secondary_colour,
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
        s.penalties_order,
        s.corners_order,
        s.direct_freekicks_order,
        mp.starts_season,
        mp.sub_appearances_season,
        mp.early_subs_season,
        mp.zero_minute_fixtures_season,
        xs.xg_share_season,
        xs.xa_share_season,
        coalesce(l.total_points_season, 0) / s.price               as points_per_million,
        xp.xpts_next_gw,
        xp.xpts_next5,
        xp.p_start_next_gw,
        xp.xpts_next5 / s.price                                    as xpts_next5_per_million,
        ng.next_gameweek,
        ng.next_gw_opponents,
        ng.next_gw_fixtures,
        ng.next_gw_expected_goals_for,
        ng.next_gw_expected_goals_against,
        ng.next_gw_clean_sheet_prob,
        n.next5_fixtures,
        n.next5_expected_goals_for,
        n.next5_clean_sheet_prob,
        l.* exclude (player_id, player_fixture_id, fixture_id, gameweek, kickoff_at, team_id,
                     is_played, minutes, total_points)
    from snapshot s
    join {{ ref('stg_fpl__teams') }} t on t.team_id = s.team_id
    left join {{ ref('team_colours') }} tc on tc.team_code = t.team_code
    left join latest l using (player_id)
    left join next5 n on n.team_code = t.team_code
    left join next_gw ng on ng.team_code = t.team_code
    left join minutes_profile mp using (player_id)
    left join xg_share xs using (player_id)
    left join xpts xp using (player_id)
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
    end as {{ col }}_rank,
    {%- endfor %}
    {%- endfor %}
    {%- for metric, direction, positions in pctl %}
    {%- set applies = "position_code in ('" ~ positions | join("', '") ~ "')" if positions else 'true' %}
    {%- set part = "position_code, is_rank_eligible and " ~ applies ~ " and " ~ metric ~ " is not null" %}
    -- Mid-rank (D71): (players worse + half of the other players tied) / (pool - 1), so a
    -- value many players share sits in the middle of its tie group, not at its bottom.
    case when is_rank_eligible and {{ applies }} and {{ metric }} is not null then
        round(100 * ((rank() over (partition by {{ part }} order by {{ metric }} {{ direction }}) - 1)
                     + 0.5 * (count(*) over (partition by {{ part }}, {{ metric }}) - 1))
              / nullif(count(*) over (partition by {{ part }}) - 1, 0))
    end as {{ metric }}_pctl{{ "," if not loop.last }}
    {%- endfor %}
from base
