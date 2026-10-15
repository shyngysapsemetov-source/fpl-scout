with latest as (
    select fixtures
    from {{ source('fpl_raw', 'fixtures') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'fixtures') }})
),

unnested as (
    select unnest(fixtures) as f
    from latest
)

select
    f.id                            as fixture_id,
    f.event                         as gameweek,      -- null while a fixture is unscheduled
    f.kickoff_time::timestamptz     as kickoff_at,
    f.team_h                        as home_team_id,
    f.team_a                        as away_team_id,
    f.team_h_score                  as home_score,
    f.team_a_score                  as away_score,
    f.team_h_difficulty             as home_difficulty,
    f.team_a_difficulty             as away_difficulty,
    f.started                       as is_started,
    f.finished                      as is_finished
from unnested
