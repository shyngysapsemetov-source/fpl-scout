-- Grain: one row per team per fixture, past seasons and this one, played or upcoming (D38).
-- Fixture difficulty split by side: what the team can expect to score and to concede, from
-- ratings as of just before kickoff (int_team_ratings).
--   expected_goals_for     = mu_venue * attack * opponent_defence
--   expected_goals_against = mu_opponent_venue * opponent_attack * defence
--   clean_sheet_prob       = P(0 goals) under a Poisson with mean expected_goals_against
-- An attacker wants a high expected_goals_for; a defender or keeper wants a high
-- clean_sheet_prob. One opponent can be easy for one and hard for the other.
with ratings as (
    select * from {{ ref('int_team_ratings') }}
),

actual as (
    select season, fixture_id, team_code, xg_for, xg_against, goals_for, goals_against
    from {{ ref('int_team_fixtures') }}
)

select
    r.season,
    r.fixture_id,
    r.kickoff_at,
    r.team_code,
    r.opponent_code,
    r.is_home,
    r.matches_rated,
    r.attack,
    r.defence,
    r.opponent_attack,
    r.opponent_defence,
    (case when r.is_home then r.mu_home else r.mu_away end)
        * r.attack * r.opponent_defence                            as expected_goals_for,
    (case when r.is_home then r.mu_away else r.mu_home end)
        * r.opponent_attack * r.defence                            as expected_goals_against,
    exp(-expected_goals_against)                                   as clean_sheet_prob,
    a.xg_for                                                       as actual_xg_for,
    a.xg_against                                                   as actual_xg_against,
    a.goals_for                                                    as actual_goals_for,
    a.goals_against                                                as actual_goals_against
from ratings r
left join actual a using (season, fixture_id, team_code)
