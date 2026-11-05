-- One row per player per fixture his club played while he was registered there, from the
-- latest full pull only: every full pull carries the full season, and the latest one includes
-- any post-match revisions. 0-minute rows are kept; marts decide how to use them.
with latest as (
    select element_id, history
    from {{ source('fpl_raw', 'element_summaries') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'element_summaries') }})
),

unnested as (
    select element_id, unnest(cast(history as json[])) as h
    from latest
)

select
    element_id                                              as player_id,
    (h->>'fixture')::int                                    as fixture_id,
    (h->>'round')::int                                      as gameweek,
    (h->>'kickoff_time')::timestamptz                       as kickoff_at,
    (h->>'was_home')::boolean                               as was_home,
    (h->>'opponent_team')::int                              as opponent_team_id,
    (h->>'minutes')::int                                    as minutes,
    (h->>'starts')::int                                     as starts,
    (h->>'total_points')::int                               as total_points,
    (h->>'goals_scored')::int                               as goals_scored,
    (h->>'assists')::int                                    as assists,
    (h->>'clean_sheets')::int                               as clean_sheets,
    (h->>'goals_conceded')::int                             as goals_conceded,
    (h->>'own_goals')::int                                  as own_goals,
    (h->>'penalties_saved')::int                            as penalties_saved,
    (h->>'penalties_missed')::int                           as penalties_missed,
    (h->>'yellow_cards')::int                               as yellow_cards,
    (h->>'red_cards')::int                                  as red_cards,
    (h->>'saves')::int                                      as saves,
    (h->>'bonus')::int                                      as bonus,
    (h->>'bps')::int                                        as bps,
    (h->>'expected_goals')::double                          as xg,
    (h->>'expected_assists')::double                        as xa,
    (h->>'expected_goal_involvements')::double              as xgi,
    (h->>'expected_goals_conceded')::double                 as xgc,
    (h->>'clearances_blocks_interceptions')::int            as cbi,
    (h->>'recoveries')::int                                 as recoveries,
    (h->>'tackles')::int                                    as tackles,
    (h->>'defensive_contribution')::int                     as defensive_contribution,
    (h->>'influence')::double                               as influence,
    (h->>'creativity')::double                              as creativity,
    (h->>'threat')::double                                  as threat,
    (h->>'ict_index')::double                               as ict_index,
    (h->>'value')::int / 10.0                               as price,
    (h->>'selected')::int                                   as selected_count,
    (h->>'transfers_in')::int                               as transfers_in,
    (h->>'transfers_out')::int                              as transfers_out,
    (h->>'modified')::boolean                               as is_modified
from unnested
