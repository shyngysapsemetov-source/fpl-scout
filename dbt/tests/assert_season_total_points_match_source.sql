-- D36: mart season totals must equal the sum of FPL's per-fixture points for each player.
with source as (
    select player_id, sum(total_points) as total_points
    from {{ ref('stg_fpl__player_fixture_stats') }}
    group by player_id
)

select m.player_id, m.total_points_season, s.total_points
from {{ ref('mart_player_current') }} m
join source s using (player_id)
where coalesce(m.total_points_season, 0) <> s.total_points
