-- D25: the category breakdown must add up to FPL's own total_points on every row. A failure
-- means a missing category, a wrong threshold in the seed (D27), or a scoring rule change.
select player_id, fixture_id, position_code, total_points, points_reconstructed
from {{ ref('int_player_fixture_points') }}
where points_reconstructed is distinct from total_points
