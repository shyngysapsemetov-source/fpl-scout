-- One row per player per fixture his club played while he was registered there, from the
-- latest fetch only: every fetch carries the full season, and the latest one includes any
-- post-match revisions. 0-minute rows are kept; marts decide how to use them.
with latest as (
    select element_id, history
    from {{ source('fpl_raw', 'element_summaries') }}
    where dt = (select max(dt) from {{ source('fpl_raw', 'element_summaries') }})
),

unnested as (
    select element_id, unnest(history) as h
    from latest
)

select
    element_id                                          as player_id,
    h.fixture                                           as fixture_id,
    h.round                                             as gameweek,
    h.kickoff_time::timestamptz                         as kickoff_at,
    h.was_home,
    h.opponent_team                                     as opponent_team_id,
    h.minutes,
    h.starts,
    h.total_points,
    h.goals_scored,
    h.assists,
    h.clean_sheets,
    h.goals_conceded,
    h.own_goals,
    h.penalties_saved,
    h.penalties_missed,
    h.yellow_cards,
    h.red_cards,
    h.saves,
    h.bonus,
    h.bps,
    h.expected_goals::double                            as xg,
    h.expected_assists::double                          as xa,
    h.expected_goal_involvements::double                as xgi,
    h.expected_goals_conceded::double                   as xgc,
    h.clearances_blocks_interceptions                   as cbi,
    h.recoveries,
    h.tackles,
    h.defensive_contribution,
    h.influence::double                                 as influence,
    h.creativity::double                                as creativity,
    h.threat::double                                    as threat,
    h.ict_index::double                                 as ict_index,
    h.value / 10.0                                      as price,
    h.selected                                          as selected_count,
    h.transfers_in,
    h.transfers_out,
    h.modified                                          as is_modified
from unnested
