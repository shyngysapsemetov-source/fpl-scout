-- D116 guard: the component features may only use information from before the deadline.
-- 1. Player windows: recompute minutes, G - xG and DC (D118) over his last 10 gameweeks (across seasons,
--    by player_code) with an explicit "earlier gameweek" join, and compare.
-- 2. Team form: the number of matches it averages must equal the count of the team's matches
--    that kicked off before the gameweek's first kickoff (rated_at), capped at 5.
with p as (
    select player_key, fixture_id, coalesce(player_code::varchar, player_key) as pk,
           (left(season, 4)::int - 2000) * 38 + gameweek as gw_idx,
           minutes, goals_scored, xg, defensive_contribution
    from {{ ref('int_player_fixtures_all') }}
),

recomputed as (
    select a.player_key, a.fixture_id,
           coalesce(sum(b.minutes), 0)               as minutes_l10,
           coalesce(sum(b.goals_scored - b.xg), 0)   as g_minus_xg_l10,
           coalesce(sum(b.defensive_contribution), 0) as dc_l10
    from p a
    left join p b on b.pk = a.pk and b.gw_idx between a.gw_idx - 10 and a.gw_idx - 1
    group by all
),

team_before as (
    select r.season, r.fixture_id, r.team_code, least(5, count(t.fixture_id)) as n_before
    from {{ ref('int_team_ratings') }} r
    left join {{ ref('int_team_fixtures') }} t
      on t.team_code = r.team_code and t.kickoff_at < r.rated_at
    group by all
)

select f.player_key, f.fixture_id, 'player window' as failed_check
from {{ ref('int_player_component_features') }} f
join recomputed r using (player_key, fixture_id)
where f.minutes_l10 <> r.minutes_l10
   or abs(f.g_minus_xg_l10 - r.g_minus_xg_l10) > 1e-6
   or f.dc_l10 <> r.dc_l10

union all

select f.player_key, f.fixture_id, 'team form window'
from {{ ref('int_player_component_features') }} f
join team_before b on b.season = f.season and b.fixture_id = f.fixture_id and b.team_code = f.team_code
where coalesce(f.team_matches_l5, 0) <> b.n_before
