-- One row per player per fetch date. Price, ownership and status exist only as current values,
-- so this table, across all dates, is their history.
with unnested as (
    select dt, _fetched_at, unnest(elements) as e
    from {{ source('fpl_raw', 'bootstrap_static') }}
)

select
    dt                                      as snapshot_date,
    _fetched_at::timestamptz                as fetched_at,
    e.id                                    as player_id,
    e.web_name,
    e.first_name,
    e.second_name,
    e.team                                  as team_id,
    e.element_type                          as position_id,
    e.now_cost / 10.0                       as price,
    e.selected_by_percent::double           as selected_by_pct,
    e.status,                               -- a available, d doubtful, i injured, s suspended, u unavailable, n not in squad
    e.chance_of_playing_next_round          as chance_of_playing_next_pct,
    e.news,
    e.transfers_in_event,
    e.transfers_out_event,
    e.total_points,
    e.minutes                               as season_minutes
from unnested
