{%- set n = var('as_of_gw', none) %}
{%- if not n %}
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
{%- else %}
-- Replay (D103): one synthetic snapshot as of the GW{{ n }} deadline. Names, position and code
-- come from the latest pull (they don't change in-season); club and price from his own
-- fixture history at GW{{ n }} (or his last fixture before it); ownership from that row's
-- `selected`. Status, injury chance, news and set-piece orders have no history before 15 Oct, so
-- they are blank: the replay can't see injuries (D103's handicap).
with latest as (
    select payload from {{ source('fpl_raw', 'bootstrap_static') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'bootstrap_static') }})
),

elements as (
    select unnest(cast(payload->'elements' as json[])) as e,
           (payload->>'total_players')::double as total_players
    from latest
),

fixtures as (
    select (f->>'id')::int as fixture_id, (f->>'team_h')::int as team_h, (f->>'team_a')::int as team_a,
           (f->>'event')::int as gameweek, (f->>'deadline_time') as deadline
    from (select unnest(cast(payload as json[])) f from {{ source('fpl_raw', 'fixtures') }}
          where dt = (select max(dt) from {{ source('fpl_raw', 'fixtures') }}))
),

history as (
    select element_id as player_id, (h->>'fixture')::int as fixture_id, (h->>'round')::int as gameweek,
           (h->>'was_home')::boolean as was_home, (h->>'value')::int / 10.0 as price,
           (h->>'selected')::double as selected, (h->>'total_points')::int as total_points,
           (h->>'minutes')::int as minutes, (h->>'kickoff_time')::timestamptz as kickoff_at
    from (select element_id, unnest(cast(history as json[])) h
          from {{ source('fpl_raw', 'element_summaries') }}
          where dt = (select max(dt) from {{ source('fpl_raw', 'element_summaries') }}))
),

as_of_row as (
    select h.*, case when h.was_home then f.team_h else f.team_a end as team_id
    from history h join fixtures f using (fixture_id)
    where h.gameweek <= {{ n }}
    qualify row_number() over (partition by h.player_id order by h.kickoff_at desc) = 1
),

before as (
    select player_id, sum(total_points) as total_points, sum(minutes) as minutes
    from history where gameweek < {{ n }} group by player_id
),

deadline as (
    select (e->>'deadline_time')::timestamptz as deadline_at
    from (select unnest(cast(payload->'events' as json[])) e from latest)
    where (e->>'id')::int = {{ n }}
)

select
    (select deadline_at::date from deadline)                as snapshot_date,
    (select deadline_at from deadline)                      as fetched_at,
    (e->>'id')::int                                         as player_id,
    (e->>'code')::int                                       as player_code,
    (e->>'web_name')::varchar                               as web_name,
    (e->>'first_name')::varchar                             as first_name,
    (e->>'second_name')::varchar                            as second_name,
    r.team_id,
    (e->>'element_type')::int                               as position_id,
    r.price,
    100 * r.selected / total_players                        as selected_by_pct,
    'a'                                                     as status,
    null::int                                               as chance_of_playing_next_pct,
    null::varchar                                           as news,
    null::int                                               as transfers_in_event,
    null::int                                               as transfers_out_event,
    coalesce(b.total_points, 0)                             as total_points,
    coalesce(b.minutes, 0)                                  as season_minutes,
    null::int                                               as penalties_order,
    null::int                                               as corners_order,
    null::int                                               as direct_freekicks_order
from elements
join as_of_row r on r.player_id = (e->>'id')::int
left join before b on b.player_id = (e->>'id')::int
{%- endif %}
