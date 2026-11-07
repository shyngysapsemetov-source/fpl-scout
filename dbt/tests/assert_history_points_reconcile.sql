-- D89: every archive row's categories, scored with that season's seeded rules and thresholds,
-- must add up to its real total_points. A failure means a wrong rule in the seed.
with rules as (
    pivot (select season, position_code, rule, points from {{ ref('scoring_rules_history') }})
    on rule using max(points) group by season, position_code
),

thresholds as (
    pivot (select season, position_code, rule, threshold from {{ ref('scoring_thresholds') }})
    on rule using max(threshold) group by season, position_code
),

scored as (
    select
        h.player_key, h.fixture_id, h.total_points,
        (case when h.minutes >= t.long_play_minutes then r.long_play
              when h.minutes > 0 then r.short_play else 0 end)
        + h.goals_scored * r.goals_scored + h.assists * r.assists
        + h.clean_sheets * r.clean_sheets
        + coalesce(h.goals_conceded // t.goals_conceded_per_point, 0) * r.goals_conceded
        + coalesce(h.saves // t.saves_per_point, 0) * r.saves
        + h.penalties_saved * r.penalties_saved + h.penalties_missed * r.penalties_missed
        + h.yellow_cards * r.yellow_cards + h.red_cards * r.red_cards
        + h.own_goals * r.own_goals + h.bonus * r.bonus
        + case when t.defensive_contribution_min is not null
                    and h.defensive_contribution >= t.defensive_contribution_min
               then r.defensive_contribution else 0 end                    as reconstructed
    from {{ ref('stg_history__player_fixtures') }} h
    join rules r on r.season = replace(h.season, '-', '_') and r.position_code = h.position_code
    left join thresholds t on t.season = replace(h.season, '-', '_') and t.position_code = h.position_code
)

select * from scored where reconstructed is distinct from total_points
