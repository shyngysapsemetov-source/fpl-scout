-- One row per player per fetch date. Price, ownership and status exist only as current values,
-- so this table, across all dates, is their history.
with unnested as (
    select dt, fetched_at, unnest(cast(payload->'elements' as json[])) as e
    from {{ source('fpl_raw', 'bootstrap_static') }}
)

select
    dt                                                      as snapshot_date,
    fetched_at,
    (e->>'id')::int                                         as player_id,
    (e->>'code')::int                                       as player_code,   -- stable across seasons
    (e->>'web_name')::varchar                               as web_name,
    (e->>'first_name')::varchar                             as first_name,
    (e->>'second_name')::varchar                            as second_name,
    (e->>'team')::int                                       as team_id,
    (e->>'element_type')::int                               as position_id,
    (e->>'now_cost')::int / 10.0                            as price,
    (e->>'selected_by_percent')::double                     as selected_by_pct,
    (e->>'status')::varchar                                 as status, -- a available, d doubtful, i injured, s suspended, u unavailable, n not in squad
    (e->>'chance_of_playing_next_round')::int               as chance_of_playing_next_pct,
    (e->>'news')::varchar                                   as news,
    (e->>'transfers_in_event')::int                         as transfers_in_event,
    (e->>'transfers_out_event')::int                        as transfers_out_event,
    (e->>'total_points')::int                               as total_points,
    (e->>'minutes')::int                                    as season_minutes,
    -- Set-piece duty, 1 = first choice; null = not on the list (D81).
    (e->>'penalties_order')::int                            as penalties_order,
    (e->>'corners_and_indirect_freekicks_order')::int       as corners_order,
    (e->>'direct_freekicks_order')::int                     as direct_freekicks_order
from unnested
