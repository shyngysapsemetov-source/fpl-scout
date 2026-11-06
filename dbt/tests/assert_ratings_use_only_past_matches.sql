-- No-leakage guard for the backtest: a fixture's ratings may only count matches that kicked
-- off before it. matches_rated must never exceed the team's finished matches before kickoff.
with prior_counts as (
    select r.season, r.fixture_id, r.team_code, r.matches_rated,
           count(m.fixture_id) as matches_before
    from {{ ref('int_team_ratings') }} r
    left join {{ ref('int_team_fixtures') }} m
      on m.season = r.season and m.team_code = r.team_code and m.kickoff_at < r.kickoff_at
    group by all
)

select * from prior_counts where matches_rated <> matches_before
