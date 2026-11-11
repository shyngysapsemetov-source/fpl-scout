{{ config(materialized="table") }}
-- Grain: one row per player per fixture (int_player_fixtures_all), past seasons and this one,
-- played and upcoming. Pre-match predictors for the per-component regressions (D116, goals and
-- assists first). Every window uses only earlier GAMEWEEKS (the deadline view, D103); team
-- windows use matches that kicked off before the gameweek's first kickoff (rated_at).
--   Player: xG/xA per 90 over the season (stabilised, from int_player_prematch_features), and
--     over his last 3 / 5 / 10 gameweeks; over-performance G - xG and A - xA over 3 / 5 / 10;
--     its trend = (G - xG over the last 3) - (G - xG over the 3 before). Player windows run
--     across seasons by player_code, so GW1 sees the end of last season.
--   Minutes: P(start), P(60+), expected minutes from the xPts minutes model.
--   Team and opponent: xG for / against and goals for / against per match over the last 5
--     (across seasons); the opponent-adjusted ratings and the fixture's expected goals.
--   Context: home / away, league position and points before the gameweek, days of rest.
--   Availability proxy: mean P(start) of each side's top 3 players by xG + xA so far.
-- D117 (v2) derived features, same pre-match rules:
--   Player: threat per 90 and share of team xG (last 5); xGI / (influence + creativity) and
--     xG / threat (penalty-taker proxy; no shot counts in the data) over the last 10; BPS per 90
--     not explained by goals and assists (last 10); role change (minutes, starts, xGI per GW,
--     last 3 vs the 10 before); spread of per-match xGI (last 10 matches played).
--   Form: matches since his last goal or assist and his run of matches with one.
--   Team / opponent: xGI the opponent conceded to his position (their last 10); defenders'
--     share of his team's xG (set pieces, last 10); team xG for / against in matches 6-15 back
--     (for a shift); PL matches in the last 14 days; points above 17th and below 5th.
--   Crowd: net transfers over the last 2 GWs, ownership and price 1 and 4 GWs back (this
--     season); FPL's own expected points for the GW (fpl_xp, archive seasons only) as a probe.
--   Availability: the opponent's usual back four (top 4 defenders by minutes, last 10) mean
--     P(start), weighted by those minutes; the opponent's likely keeper's goals conceded - xGC
--     over his last 10 GWs.
-- D118 defensive contribution (DC, the action count: DEF tackles + CBI, MID/FWD + recoveries;
--   only recorded from 2025/26): his DC and the minutes where DC was recorded, over the season
--   so far and his last 3 / 5 / 10 gameweeks; per match, the DC his team makes (last 5, a
--   possession proxy: more defending = less of the ball) and the DC that players in his
--   position make against the opponent (their last 5).
-- Targets (null for upcoming fixtures): goals_scored, assists, minutes, defensive_contribution.

with p as (
    select
        *,
        coalesce(player_code::varchar, player_key)                        as pk,
        (left(season, 4)::int - 2000) * 38 + gameweek                     as gw_idx,
        minutes > 0                                                       as played,
        goals_scored + assists > 0                                        as ret,
        -- BPS not from goals and assists (goal BPS: GKP/DEF 12, MID 18, FWD 24; assist 9).
        bps - goals_scored * case position_code when 'MID' then 18 when 'FWD' then 24 else 12 end
            - assists * 9                                                 as bps_ex
    from {{ ref('int_player_fixtures_all') }}
),

-- Running count of matches played, up to and including this gameweek.
p2 as (
    select *,
           count(*) filter (where played) over (partition by pk order by gw_idx
                                                range between unbounded preceding and current row) as played_incl
    from p
),

player_windows as (
    select
        season, player_key, fixture_id,
        {%- for n in [3, 5, 10] %}
        coalesce(sum(minutes) over w{{ n }}, 0)                           as minutes_l{{ n }},
        coalesce(sum(xg) over w{{ n }}, 0)                                as xg_l{{ n }},
        coalesce(sum(xa) over w{{ n }}, 0)                                as xa_l{{ n }},
        coalesce(sum(goals_scored - xg) over w{{ n }}, 0)                 as g_minus_xg_l{{ n }},
        coalesce(sum(assists - xa) over w{{ n }}, 0)                      as a_minus_xa_l{{ n }},
        {%- endfor %}
        coalesce(sum(goals_scored - xg) over w_prev3, 0)                  as g_minus_xg_prev3,
        coalesce(sum(assists - xa) over w_prev3, 0)                       as a_minus_xa_prev3,
        -- v2 (D117)
        coalesce(sum(threat) over w5, 0)                                  as threat_l5,
        -- D118: DC and the minutes it was recorded in (null before 2025/26)
        {%- for n in [3, 5, 10] %}
        coalesce(sum(defensive_contribution) over w{{ n }}, 0)            as dc_l{{ n }},
        coalesce(sum(minutes) filter (where defensive_contribution is not null) over w{{ n }}, 0) as dc_minutes_l{{ n }},
        {%- endfor %}
        coalesce(sum(defensive_contribution) over s_prior, 0)             as dc_season,
        coalesce(sum(minutes) filter (where defensive_contribution is not null) over s_prior, 0) as dc_minutes_season,
        coalesce(sum(threat) over w10, 0)                                 as threat_l10,
        coalesce(sum(influence + creativity) over w10, 0)                 as ic_l10,
        coalesce(sum(bps_ex) over w10, 0)                                 as bps_ex_l10,
        coalesce(sum(goals_conceded - xgc) over w10, 0)                   as gc_minus_xgc_l10,
        coalesce(sum(minutes) over w_prev10, 0)                           as minutes_prev10,
        coalesce(sum(starts) over w3, 0)                                  as starts_l3,
        coalesce(sum(starts) over w_prev10, 0)                            as starts_prev10,
        coalesce(sum(xg + xa) over w_prev10, 0)                           as xgi_prev10,
        stddev_samp(xg + xa) filter (where played) over w10              as xgi_sd_l10,
        coalesce(count(*) filter (where played) over w_all, 0)            as played_before,
        coalesce(count(*) filter (where played) over w_all, 0)
          - coalesce(max(case when ret then played_incl end) over w_all, 0) as matches_since_return,
        coalesce(count(*) filter (where played) over w_all, 0)
          - coalesce(max(case when played and not ret then played_incl end) over w_all, 0) as return_streak,
        -- Crowd, this season only (by player_key and gameweek).
        coalesce(sum(transfers_balance) over s2, 0)                       as net_transfers_l2,
        max(selected) over s1                                             as selected_l1,
        max(price) over s1                                                as price_l1,
        max(price) over s4                                                as price_l4
    from p2
    window w3      as (partition by pk order by gw_idx range between 3 preceding and 1 preceding),
           w5      as (partition by pk order by gw_idx range between 5 preceding and 1 preceding),
           w10     as (partition by pk order by gw_idx range between 10 preceding and 1 preceding),
           w_prev3 as (partition by pk order by gw_idx range between 6 preceding and 4 preceding),
           w_prev10 as (partition by pk order by gw_idx range between 13 preceding and 4 preceding),
           w_all   as (partition by pk order by gw_idx range between unbounded preceding and 1 preceding),
           s1      as (partition by player_key order by gameweek range between 1 preceding and 1 preceding),
           s_prior as (partition by player_key order by gameweek range between unbounded preceding and 1 preceding),
           s2      as (partition by player_key order by gameweek range between 2 preceding and 1 preceding),
           s4      as (partition by player_key order by gameweek range between 4 preceding and 4 preceding)
),

ratings as (
    select season, fixture_id, team_code, opponent_code, rated_at, attack, defence,
           opponent_attack, opponent_defence, mu_home, mu_away
    from {{ ref('int_team_ratings') }}
),

team_matches as (
    select season, fixture_id, team_code, kickoff_at, xg_for, xg_against, goals_for, goals_against,
           case when goals_for > goals_against then 3 when goals_for = goals_against then 1 else 0 end as pts
    from {{ ref('int_team_fixtures') }}
),

-- Last 5 matches before the deadline, across seasons (same team_code).
team_form as (
    select season, fixture_id, team_code,
           count(*) filter (where rn <= 5)                  as team_matches_l5,
           avg(xg_for) filter (where rn <= 5)               as team_xg_for_l5,
           avg(xg_against) filter (where rn <= 5)           as team_xg_against_l5,
           avg(goals_for) filter (where rn <= 5)            as team_goals_for_l5,
           avg(goals_against) filter (where rn <= 5)        as team_goals_against_l5,
           avg(xg_for) filter (where rn > 5)                as team_xg_for_6_15,
           avg(xg_against) filter (where rn > 5)            as team_xg_against_6_15
    from (
        select r.season, r.fixture_id, r.team_code, t.xg_for, t.xg_against, t.goals_for, t.goals_against,
               row_number() over (partition by r.season, r.fixture_id, r.team_code
                                  order by t.kickoff_at desc) as rn
        from ratings r
        join team_matches t on t.team_code = r.team_code and t.kickoff_at < r.rated_at
    )
    where rn <= 15
    group by all
),

-- League table before each gameweek's deadline, this season only.
cutoffs as (
    select distinct r.season, r.rated_at, t.team_code
    from ratings r
    join (select distinct season, team_code from ratings) t using (season)
),

table_at as (
    select c.season, c.rated_at, c.team_code,
           count(m.fixture_id)                                 as played,
           coalesce(sum(m.pts), 0)                             as league_points,
           coalesce(sum(m.goals_for - m.goals_against), 0)     as goal_diff,
           coalesce(sum(m.goals_for), 0)                       as goals_for
    from cutoffs c
    left join team_matches m on m.season = c.season and m.team_code = c.team_code
                            and m.kickoff_at < c.rated_at
    group by all
),

standings0 as (
    select *,
           case when played = 0 then 10.5
                else rank() over (partition by season, rated_at
                                  order by league_points desc, goal_diff desc, goals_for desc) end
                                                               as league_position,
           row_number() over (partition by season, rated_at
                              order by league_points desc, goal_diff desc, goals_for desc, team_code) as table_row
    from table_at
),

standings as (
    select *,
           league_points - max(league_points) filter (where table_row = 17)
               over (partition by season, rated_at)            as points_above_17th,
           max(league_points) filter (where table_row = 5)
               over (partition by season, rated_at) - league_points as points_below_5th
    from standings0
),

-- Days since the team's previous league kickoff (capped at 14; the summer counts as 14), and
-- league matches in the 14 days before (cup matches aren't in the data).
rest as (
    select season, fixture_id, team_code,
           least(14, coalesce(date_diff('hour', lag(kickoff_at) over (partition by team_code order by kickoff_at),
                                         kickoff_at) / 24.0, 14))      as rest_days,
           count(*) over (partition by team_code order by kickoff_at
                          range between interval 14 days preceding and interval 1 second preceding) as matches_l14
    from (
        select season, fixture_id, home_code as team_code, kickoff_at from {{ ref('int_fixture_schedule') }}
        union all
        select season, fixture_id, away_code, kickoff_at from {{ ref('int_fixture_schedule') }}
    )
),

-- D119: the minutes model's outputs come from the intermediate model, not the xPts mart.
e as (
    select season, player_key, fixture_id, p_start, p_60, p_early, p_sub, exp_minutes
    from {{ ref('int_player_minutes_model') }}
),

f as (
    select * from {{ ref('int_player_prematch_features') }}
),

-- Availability proxy: each side's top 3 by xG + xA so far (last season's, fading over the
-- first 6 fixtures as in D93, until this season has data), and their mean P(start).
key_players as (
    select season, fixture_id, team_code, avg(p_start) as top3_p_start
    from (
        select f.season, f.fixture_id, f.team_code, e.p_start,
               row_number() over (partition by f.season, f.fixture_id, f.team_code
                                  order by f.xg_prior + f.xa_prior
                                           + coalesce(f.prev_xg + f.prev_xa, 0)
                                             * greatest(0, 1 - f.fixtures_prior / 6) desc,
                                           f.player_key) as rn
        from f
        join e using (season, player_key, fixture_id)
    )
    where rn <= 3
    group by all
),

-- xGI each team conceded to each outfield position, per match, and each team's xG by position.
pos_xgi as (
    select season, fixture_id, kickoff_at, team_code, opponent_code, position_code,
           sum(xg + xa) as xgi, sum(xg) as xg
    from p
    where played and position_code <> 'GKP'
    group by all
),

-- D118: DC per team per match, by position (only where DC is recorded).
pos_dc as (
    select season, fixture_id, kickoff_at, team_code, opponent_code, position_code,
           sum(defensive_contribution) as dc
    from p
    where played and defensive_contribution is not null
    group by all
),

team_dc as (
    select season, fixture_id, team_code, avg(dc) as team_dc_made_l5
    from (
        select r.season, r.fixture_id, r.team_code, x.fixture_id as fx, sum(x.dc) as dc,
               row_number() over (partition by r.season, r.fixture_id, r.team_code
                                  order by max(x.kickoff_at) desc) as rn
        from ratings r
        join pos_dc x on x.team_code = r.team_code and x.kickoff_at < r.rated_at
        group by r.season, r.fixture_id, r.team_code, x.season, x.fixture_id
    )
    where rn <= 5
    group by all
),

-- DC made against the opponent by players in his position, per match (their last 5).
opp_dc as (
    select season, fixture_id, team_code, position_code, avg(dc) as opp_dc_forced_to_pos_l5
    from (
        select r.season, r.fixture_id, r.team_code, x.position_code, x.dc,
               row_number() over (partition by r.season, r.fixture_id, r.team_code, x.position_code
                                  order by x.kickoff_at desc) as rn
        from ratings r
        join pos_dc x on x.opponent_code = r.opponent_code and x.kickoff_at < r.rated_at
    )
    where rn <= 5
    group by all
),

-- The opponent's last 10 matches: xGI conceded to his position.
opp_leak as (
    select season, fixture_id, team_code, position_code, avg(xgi) as opp_xgi_conceded_to_pos_l10
    from (
        select r.season, r.fixture_id, r.team_code, x.position_code, x.xgi,
               row_number() over (partition by r.season, r.fixture_id, r.team_code, x.position_code
                                  order by x.kickoff_at desc) as rn
        from ratings r
        join pos_xgi x on x.opponent_code = r.opponent_code and x.kickoff_at < r.rated_at
    )
    where rn <= 10
    group by all
),

-- His team's last 10 matches: defenders' share of the team's xG (a set-piece proxy).
set_pieces as (
    select season, fixture_id, team_code,
           sum(xg) filter (where position_code = 'DEF') / nullif(sum(xg), 0) as team_def_xg_share_l10
    from (
        select r.season, r.fixture_id, r.team_code, x.position_code, x.xg,
               dense_rank() over (partition by r.season, r.fixture_id, r.team_code
                                  order by x.kickoff_at desc) as rn
        from ratings r
        join pos_xgi x on x.team_code = r.team_code and x.kickoff_at < r.rated_at
    )
    where rn <= 10
    group by all
),

-- The opponent's usual back four and likely keeper, from the rows registered to each side.
back_four as (
    select season, fixture_id, team_code,
           sum(p_start * minutes_l10) / nullif(sum(minutes_l10), 0) as back_four_p_start
    from (
        select f.season, f.fixture_id, f.team_code, e.p_start, w.minutes_l10,
               row_number() over (partition by f.season, f.fixture_id, f.team_code
                                  order by w.minutes_l10 desc, f.player_key) as rn
        from f
        join e using (season, player_key, fixture_id)
        join player_windows w using (season, player_key, fixture_id)
        where f.position_code = 'DEF'
    )
    where rn <= 4
    group by all
),

keeper as (
    select season, fixture_id, team_code, gc_minus_xgc_l10 as gk_gc_minus_xgc_l10
    from (
        select f.season, f.fixture_id, f.team_code, w.gc_minus_xgc_l10,
               row_number() over (partition by f.season, f.fixture_id, f.team_code
                                  order by e.p_start desc, w.minutes_l10 desc, f.player_key) as rn
        from f
        join e using (season, player_key, fixture_id)
        join player_windows w using (season, player_key, fixture_id)
        where f.position_code = 'GKP'
    )
    where rn = 1
),

-- D119: position DC per 90 over all earlier gameweeks where DC was recorded (the prior for his
-- DC rate), as of each gameweek.
pos_dc_cum as (
    select position_code, gw_idx,
           sum(dc) over w / nullif(sum(mins) over w, 0) * 90 as pos_dc90
    from (
        select position_code, gw_idx, sum(defensive_contribution) as dc, sum(minutes) as mins
        from p
        where played and defensive_contribution is not null
        group by position_code, gw_idx
    )
    window w as (partition by position_code order by gw_idx)
),

base as (
select
    (left(f.season, 4)::int - 2000) * 38 + f.gameweek                   as gw_idx,
    f.season, f.player_key, f.player_id, f.player_code, f.player_name, f.position_code,
    f.fixture_id, f.gameweek, f.kickoff_at, f.team_code, f.opponent_code, f.is_upcoming,
    f.was_home::int                                                     as is_home,
    -- player level
    f.xg_per90_season, f.xa_per90_season, f.xg_per90 as xg_per90_form, f.xa_per90 as xa_per90_form,
    f.minutes_prior, f.fixtures_prior,
    {%- for n in [3, 5, 10] %}
    w.minutes_l{{ n }}, w.xg_l{{ n }}, w.xa_l{{ n }},
    w.g_minus_xg_l{{ n }}, w.a_minus_xa_l{{ n }},
    {%- endfor %}
    w.g_minus_xg_l3 - w.g_minus_xg_prev3                                as g_minus_xg_trend,
    w.a_minus_xa_l3 - w.a_minus_xa_prev3                                as a_minus_xa_trend,
    -- minutes model
    e.p_start, e.p_60, e.p_early, e.p_sub, e.exp_minutes,
    -- team and opponent
    tf.team_matches_l5, tf.team_xg_for_l5, tf.team_xg_against_l5,
    tf.team_goals_for_l5, tf.team_goals_against_l5,
    ofm.team_xg_for_l5 as opp_xg_for_l5, ofm.team_xg_against_l5 as opp_xg_against_l5,
    ofm.team_goals_for_l5 as opp_goals_for_l5, ofm.team_goals_against_l5 as opp_goals_against_l5,
    r.attack, r.defence, r.opponent_attack, r.opponent_defence,
    x.expected_goals_for, x.expected_goals_against, x.clean_sheet_prob,
    x.expected_goals_for / nullif(r.attack * (r.mu_home + r.mu_away) / 2, 0) as attack_factor,
    -- context
    st.league_position, so.league_position as opp_league_position,
    st.league_points - so.league_points                                 as points_gap,
    rs.rest_days,
    kp.top3_p_start as own_top3_p_start, ko.top3_p_start as opp_top3_p_start,
    -- v2 (D117)
    w.threat_l5, w.threat_l10, w.ic_l10, w.bps_ex_l10, w.minutes_prev10, w.starts_l3,
    w.starts_prev10, w.xgi_prev10, w.xgi_sd_l10, w.played_before,
    least(w.matches_since_return, 20)                                   as matches_since_return,
    least(w.return_streak, 10)                                          as return_streak,
    w.net_transfers_l2, w.selected_l1, w.price_l1, w.price_l4,
    f.fpl_xp,
    tf.team_xg_for_6_15, tf.team_xg_against_6_15,
    ofm.team_xg_for_6_15 as opp_xg_for_6_15, ofm.team_xg_against_6_15 as opp_xg_against_6_15,
    ol.opp_xgi_conceded_to_pos_l10, sp.team_def_xg_share_l10,
    rs.matches_l14, st.points_above_17th, st.points_below_5th,
    bf.back_four_p_start as opp_back_four_p_start,
    kg.gk_gc_minus_xgc_l10 as opp_gk_gc_minus_xgc_l10,
    -- D118
    {%- for n in [3, 5, 10] %}
    w.dc_l{{ n }}, w.dc_minutes_l{{ n }},
    {%- endfor %}
    w.dc_season, w.dc_minutes_season,
    td.team_dc_made_l5, otd.team_dc_made_l5 as opp_dc_made_l5, od.opp_dc_forced_to_pos_l5,
    f.defensive_contribution,
    -- targets
    f.goals_scored, f.assists, f.minutes, f.total_points
from f
join e using (season, player_key, fixture_id)
join player_windows w using (season, player_key, fixture_id)
join ratings r on r.season = f.season and r.fixture_id = f.fixture_id and r.team_code = f.team_code
join {{ ref('fct_team_fixture_expectations') }} x
  on x.season = f.season and x.fixture_id = f.fixture_id and x.team_code = f.team_code
left join team_form tf on tf.season = f.season and tf.fixture_id = f.fixture_id and tf.team_code = f.team_code
left join team_form ofm on ofm.season = f.season and ofm.fixture_id = f.fixture_id and ofm.team_code = f.opponent_code
left join standings st on st.season = f.season and st.rated_at = r.rated_at and st.team_code = f.team_code
left join standings so on so.season = f.season and so.rated_at = r.rated_at and so.team_code = f.opponent_code
left join rest rs on rs.season = f.season and rs.fixture_id = f.fixture_id and rs.team_code = f.team_code
left join key_players kp on kp.season = f.season and kp.fixture_id = f.fixture_id and kp.team_code = f.team_code
left join key_players ko on ko.season = f.season and ko.fixture_id = f.fixture_id and ko.team_code = f.opponent_code
left join opp_leak ol on ol.season = f.season and ol.fixture_id = f.fixture_id and ol.team_code = f.team_code
                     and ol.position_code = f.position_code
left join set_pieces sp on sp.season = f.season and sp.fixture_id = f.fixture_id and sp.team_code = f.team_code
left join back_four bf on bf.season = f.season and bf.fixture_id = f.fixture_id and bf.team_code = f.opponent_code
left join team_dc td on td.season = f.season and td.fixture_id = f.fixture_id and td.team_code = f.team_code
left join team_dc otd on otd.season = f.season and otd.fixture_id = f.fixture_id and otd.team_code = f.opponent_code
left join opp_dc od on od.season = f.season and od.fixture_id = f.fixture_id and od.team_code = f.team_code
                   and od.position_code = f.position_code
left join keeper kg on kg.season = f.season and kg.fixture_id = f.fixture_id and kg.team_code = f.opponent_code
),

with_prior as (
    select b.*, pd.pos_dc90 as pos_dc90_prior
    from base b
    asof left join pos_dc_cum pd on pd.position_code = b.position_code and b.gw_idx > pd.gw_idx
),

dc_rates as (
    select *,
           (dc_season + 3 * pos_dc90_prior) / (dc_minutes_season / 90 + 3)     as dc90_season
    from with_prior
)

-- D119: model-ready terms (the names the fitted seeds use; NULLs are filled with the training
-- median when the model is applied).
select
    *,
    {%- for m in ['xg', 'xa'] %}
    ln({{ m }}_per90_season + 0.01)                                      as log_{{ m }}90_season,
    {%- for n in [3, 5] %}
    ln(({{ m }}_l{{ n }} + {{ m }}_per90_season) / (minutes_l{{ n }} / 90 + 1) + 0.01) as log_{{ m }}90_l{{ n }},
    {%- endfor %}
    {%- endfor %}
    ln(1 + minutes_prior / 90)                                          as log_minutes_prior,
    ln(expected_goals_for)                                              as log_exp_goals_for,
    ln(coalesce(team_xg_for_l5, expected_goals_for))                    as log_team_xg_for_l5,
    ln(coalesce(opp_xg_against_l5, expected_goals_for))                 as log_opp_xg_against_l5,
    ln(coalesce(opp_xg_for_l5, expected_goals_against))                 as log_opp_xg_for_l5,
    threat_l5 / (minutes_l5 / 90 + 1)                                   as threat90_l5,
    (xg_l10 + xa_l10) / (ic_l10 / 100 + 0.5)                            as xgi_per_ic_l10,
    xg_l10 / (threat_l10 / 100 + 0.5)                                   as xg_per_threat_l10,
    expected_goals_for - expected_goals_against                         as exp_goal_diff,
    (expected_goals_for - expected_goals_against) * (2 * is_home - 1)   as gap_x_home,
    sign(net_transfers_l2 / greatest(coalesce(selected_l1, 0), 1000))
      * ln(1 + abs(net_transfers_l2 / greatest(coalesce(selected_l1, 0), 1000))) as net_transfer_ratio,
    minutes_l3 / 3 - minutes_prev10 / 10                                as role_min_delta,
    starts_l3 / 3 - starts_prev10 / 10                                  as role_start_delta,
    ln(dc90_season + 0.1)                                               as log_dc90_season,
    ln((dc_l5 + dc90_season) / (dc_minutes_l5 / 90 + 1) + 0.1)          as log_dc90_l5,
    ln((dc_l3 + dc90_season) / (dc_minutes_l3 / 90 + 1) + 0.1)          as log_dc90_l3,
    ln(1 + dc_minutes_season / 90)                                      as log_dc_minutes_season,
    ln(opp_dc_forced_to_pos_l5 + 1)                                     as log_opp_dc_forced,
    ln(team_dc_made_l5 + 1)                                             as log_team_dc_made,
    ln(opp_dc_made_l5 + 1)                                              as log_opp_dc_made
from dc_rates
