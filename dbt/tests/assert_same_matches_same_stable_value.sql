-- D72 guard: when last 10 holds exactly the season's matches, the stabilised values must
-- agree. Before D72 they could differ (seventh error in the ledger).
select player_fixture_id, points_per_match_l10_stable, points_per_match_season_stable,
       xg_per90_l10_stable, xg_per90_season_stable
from {{ ref('fct_player_rolling') }}
where matches_played_l10 = matches_played_season
  and (abs(points_per_match_l10_stable - points_per_match_season_stable) > 1e-9
       or abs(xg_per90_l10_stable - xg_per90_season_stable) > 1e-9
       or abs(minutes_per_match_l10_stable - minutes_per_match_season_stable) > 1e-9)
