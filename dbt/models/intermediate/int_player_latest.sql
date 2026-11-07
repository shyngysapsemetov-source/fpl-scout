-- Grain: one row per player: his latest snapshot (club, position, price, availability).
-- The xPts chain reads players from here rather than mart_player_current, so the mart can
-- show xPts without a dependency cycle.
select s.*, p.position_code
from {{ ref('stg_fpl__player_snapshots') }} s
join {{ ref('stg_fpl__positions') }} p using (position_id)
qualify row_number() over (partition by s.player_id order by s.snapshot_date desc) = 1
