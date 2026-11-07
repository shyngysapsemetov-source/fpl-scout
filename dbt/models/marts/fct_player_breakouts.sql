{{ config(materialized="table") }}
-- Grain: one row per player per played-or-scheduled fixture, past seasons and this one (D105,
-- D107). As of the end of that fixture: is the player in a breakout, why, and how likely it is
-- to last, from the form analysis's definition:
--   recent   = his last 4 fixtures including this one (needs 120+ minutes)
--   baseline = the 15 fixtures before those (needs 300+ minutes), across seasons by player_code
--   flag     = recent points per fixture - baseline >= 2.0
--   reason (first that applies): moved (club or position changed within the last 10
--     fixtures) > role (minutes per fixture +25, or start share +0.4) > underlying (xG+xA per
--     90 +0.15 and 1.4x baseline, outfield) > defensive (DEF/GKP clean-sheet rate +0.3) >
--     luck (goals + assists exceed xG + xA by 1.0+ over the window) > other.
-- Persistence odds per reason come from flags in seasons BEFORE the current one only: the
-- share whose next 5 fixtures stayed above baseline, and the share of the jump kept.
{%- set this_season = var('season') | replace('_', '-') %}

with rows as (
    select *
    from {{ ref('int_player_fixtures_all') }}
    where not is_upcoming and player_code is not null
),

w as (
    select
        *,
        sum(minutes) over recent                                   as r_min,
        count(*) over recent                                       as r_n,
        sum(total_points) over recent                              as r_pts,
        sum(starts) over recent                                    as r_starts,
        sum(xg + xa) over recent                                   as r_xgi,
        sum(goals_scored + assists) over recent                    as r_ga,
        sum(clean_sheets) over recent                              as r_cs,
        sum(minutes) over base                                     as b_min,
        count(*) over base                                         as b_n,
        sum(total_points) over base                                as b_pts,
        sum(starts) over base                                      as b_starts,
        sum(xg + xa) over base                                     as b_xgi,
        sum(clean_sheets) over base                                as b_cs,
        count(distinct team_code) over last10                      as teams_last10,
        count(distinct position_code) over last10                  as positions_last10,
        -- outcome: his next 5 fixtures (for persistence rates, past seasons only)
        sum(total_points) over next5                               as n_pts,
        count(*) over next5                                        as n_n
    from rows
    window recent as (partition by player_code order by kickoff_at rows between 3 preceding and current row),
           base   as (partition by player_code order by kickoff_at rows between 18 preceding and 4 preceding),
           last10 as (partition by player_code order by kickoff_at rows between 9 preceding and current row),
           next5  as (partition by player_code order by kickoff_at rows between 1 following and 5 following)
),

flags as (
    select
        *,
        r_pts / r_n                                                as recent_ppf,
        b_pts / nullif(b_n, 0)                                     as baseline_ppf,
        r_n = 4 and r_min >= 120 and b_n >= 8 and b_min >= 300
            and r_pts / r_n - b_pts / b_n >= 2.0                   as is_breakout
    from w
),

reasons as (
    select
        *,
        case when not is_breakout then null
             when teams_last10 > 1 or positions_last10 > 1 then 'moved'
             when r_min / r_n - b_min / b_n >= 25
               or r_starts / r_n - b_starts / b_n >= 0.4 then 'role'
             when position_code <> 'GKP'
               and r_xgi / nullif(r_min, 0) * 90 - b_xgi / nullif(b_min, 0) * 90 >= 0.15
               and r_xgi / nullif(r_min, 0) >= 1.4 * b_xgi / nullif(b_min, 0) then 'underlying'
             when position_code in ('DEF', 'GKP')
               and r_cs / r_n - b_cs / b_n >= 0.3 then 'defensive'
             when r_ga - r_xgi >= 1.0 then 'luck'
             else 'other' end                                      as breakout_reason
    from flags
),

-- Persistence by reason, learned from earlier seasons' flags (no leakage into this season).
persistence as (
    select
        breakout_reason,
        count(*)                                                   as past_flags,
        avg((n_pts / n_n > baseline_ppf)::int)                     as p_stays_above_next5,
        avg(least(greatest((n_pts / n_n - baseline_ppf) / nullif(recent_ppf - baseline_ppf, 0), -1), 1.5))
                                                                   as share_of_jump_kept_next5
    from reasons
    where is_breakout and n_n = 5 and season <> '{{ this_season }}'
    group by breakout_reason
)

select
    r.season, r.player_key, r.player_code, r.player_id, r.player_name, r.position_code,
    r.team_code, r.fixture_id, r.gameweek, r.kickoff_at,
    r.is_breakout, r.breakout_reason,
    round(r.recent_ppf, 2)                                         as recent_points_per_fixture,
    round(r.baseline_ppf, 2)                                       as baseline_points_per_fixture,
    p.p_stays_above_next5,
    p.share_of_jump_kept_next5,
    p.past_flags
from reasons r
left join persistence p using (breakout_reason)
