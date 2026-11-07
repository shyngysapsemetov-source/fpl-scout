-- D86 guard: a fixture's pre-match features may only count earlier fixtures. matches_prior
-- must equal the number of matches he played that kicked off before this one.
with checks as (
    select f.player_key, f.fixture_id, f.matches_prior,
           count(e.fixture_id) as played_before
    from {{ ref('int_player_prematch_features') }} f
    left join {{ ref('int_player_fixtures_all') }} e
      on e.player_key = f.player_key and e.kickoff_at < f.kickoff_at and e.minutes > 0
    group by all
)

select * from checks where matches_prior <> played_before
