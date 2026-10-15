-- team_id is derived from the fixture and was_home. The API also gives the opponent directly,
-- so the other side of the fixture must equal it. A failure means the derivation is wrong.
select f.player_fixture_id
from {{ ref('fct_player_fixtures') }} f
join {{ ref('stg_fpl__fixtures') }} x using (fixture_id)
where f.opponent_team_id
   <> case when f.was_home then x.away_team_id else x.home_team_id end
