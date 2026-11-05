with latest as (
    select payload
    from {{ source('fpl_raw', 'fixtures') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'fixtures') }})
),

unnested as (
    select unnest(cast(payload as json[])) as f
    from latest
)

select
    (f->>'id')::int                                         as fixture_id,
    (f->>'event')::int                                      as gameweek, -- null while a fixture is unscheduled
    (f->>'kickoff_time')::timestamptz                       as kickoff_at,
    (f->>'team_h')::int                                     as home_team_id,
    (f->>'team_a')::int                                     as away_team_id,
    (f->>'team_h_score')::int                               as home_score,
    (f->>'team_a_score')::int                               as away_score,
    (f->>'team_h_difficulty')::int                          as home_difficulty,
    (f->>'team_a_difficulty')::int                          as away_difficulty,
    (f->>'started')::boolean                                as is_started,
    (f->>'finished')::boolean                               as is_finished
from unnested
