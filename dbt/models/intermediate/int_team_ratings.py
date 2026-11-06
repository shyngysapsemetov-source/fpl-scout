"""Team attack and defence ratings as of just before every fixture (D38, D41, D44-D47).

Grain: one row per team per fixture, past seasons and this one, played or upcoming.

Model (multiplicative, D45): expected xG for team t against o at venue v is
    mu_v * attack[t] * defence[o]
where mu_v is the league's average xG at that venue (home or away), attack > 1 means the team
creates more than average, and defence > 1 means it concedes more (it's a weakness). One
rating per side per team; the home edge is league-wide (mu_home / mu_away).

Each fixture is rated only from matches that kicked off before it, so nothing leaks into a
backtest. Within the season, a team's matches are weighted 0.5 ** (age / HALF_LIFE), with age
counted in that team's own matches back from the latest (D44). Last season's final ratings act
as a prior worth PRIOR_MATCHES matches (D46); promoted teams start from the average of last
season's relegated teams. The first season loaded has a neutral prior of 1.0. Ratings are
opponent-adjusted by iterating until they converge (D47), then normalised to mean 1.
"""
import numpy as np
import pandas as pd

HALF_LIFE = 6        # matches (D44); tuned later in the backtest
PRIOR_MATCHES = 6    # how many matches last season's rating is worth (D46)
MAX_ITER = 100
TOL = 1e-7


def fit(played, teams, prior_att, prior_def, prior_mu):
    """Ratings from the played matches of one season so far. `played` has one row per team per
    fixture (team_code, opponent_code, is_home, xg_for, xg_against, weight)."""
    mu_h0, mu_a0 = prior_mu
    k = PRIOR_MATCHES
    att = prior_att.reindex(teams).astype(float)
    dfn = prior_def.reindex(teams).astype(float)
    if played.empty:
        return att, dfn, mu_h0, mu_a0

    # League venue averages: this season's weighted xG plus a prior worth k rounds of matches.
    n_prior = k * len(teams) / 2
    home = played[played.is_home]
    away = played[~played.is_home]
    mu_h = (home.xg_for.sum() + n_prior * mu_h0) / (len(home) + n_prior)
    mu_a = (away.xg_for.sum() + n_prior * mu_a0) / (len(away) + n_prior)
    mu_bar = (mu_h + mu_a) / 2

    w = played.weight.to_numpy()
    mu_for = np.where(played.is_home, mu_h, mu_a)       # venue of the team's own attack
    mu_against = np.where(played.is_home, mu_a, mu_h)   # venue of the opponent's attack
    team = played.team_code.to_numpy()
    opp = played.opponent_code.to_numpy()
    xgf = played.xg_for.to_numpy()
    xga = played.xg_against.to_numpy()
    pa = prior_att.reindex(teams).to_numpy(float)
    pd_ = prior_def.reindex(teams).to_numpy(float)
    idx = {t: i for i, t in enumerate(teams)}
    ti = np.array([idx[t] for t in team])
    oi = np.array([idx[t] for t in opp])
    a = att.to_numpy(float)
    d = dfn.to_numpy(float)

    for _ in range(MAX_ITER):
        num_a = np.bincount(ti, w * xgf, len(teams)) + k * mu_bar * pa
        den_a = np.bincount(ti, w * mu_for * d[oi], len(teams)) + k * mu_bar
        num_d = np.bincount(ti, w * xga, len(teams)) + k * mu_bar * pd_
        den_d = np.bincount(ti, w * mu_against * a[oi], len(teams)) + k * mu_bar
        a_new = num_a / den_a
        d_new = num_d / den_d
        a_new /= a_new.mean()
        d_new /= d_new.mean()
        done = max(abs(a_new - a).max(), abs(d_new - d).max()) < TOL
        a, d = a_new, d_new
        if done:
            break
    return pd.Series(a, teams), pd.Series(d, teams), mu_h, mu_a


def weights(played):
    """0.5 ** (age / HALF_LIFE), age = how many of the team's own matches came after this one."""
    age = played.groupby("team_code").kickoff_at.rank(ascending=False, method="first") - 1
    return 0.5 ** (age / HALF_LIFE)


def model(dbt, session):
    dbt.config(materialized="table")
    matches = dbt.ref("int_team_fixtures").df()
    schedule = dbt.ref("int_fixture_schedule").df()

    rows = []
    prior_att = prior_def = None
    prior_mu = (1.5, 1.3)    # neutral starting league averages for the first season only
    prev_teams = set()
    for season in sorted(schedule.season.unique()):
        sched = schedule[schedule.season == season].sort_values("kickoff_at")
        teams = sorted(set(sched.home_code) | set(sched.away_code))
        if prior_att is None:
            prior_att = pd.Series(1.0, teams)
            prior_def = pd.Series(1.0, teams)
        else:
            relegated = sorted(prev_teams - set(teams))
            promoted = [t for t in teams if t not in prev_teams]
            for t in promoted:
                prior_att[t] = prior_att[relegated].mean() if relegated else 1.0
                prior_def[t] = prior_def[relegated].mean() if relegated else 1.0
            prior_att, prior_def = prior_att.reindex(teams), prior_def.reindex(teams)

        season_matches = matches[matches.season == season]
        cache = {}
        for kickoff in sched.kickoff_at.unique():
            if kickoff not in cache:
                played = season_matches[season_matches.kickoff_at < kickoff].copy()
                played["weight"] = weights(played) if len(played) else []
                cache[kickoff] = fit(played, teams, prior_att, prior_def, prior_mu) + (
                    played.groupby("team_code").size(),)
            att, dfn, mu_h, mu_a, n = cache[kickoff]
            for f in sched[sched.kickoff_at == kickoff].itertuples():
                for code, opp, is_home in ((f.home_code, f.away_code, True),
                                           (f.away_code, f.home_code, False)):
                    rows.append({
                        "season": season, "fixture_id": f.fixture_id,
                        "kickoff_at": f.kickoff_at, "team_code": code,
                        "opponent_code": opp, "is_home": is_home,
                        "matches_rated": int(n.get(code, 0)),
                        "attack": att[code], "defence": dfn[code],
                        "opponent_attack": att[opp], "opponent_defence": dfn[opp],
                        "mu_home": mu_h, "mu_away": mu_a,
                    })

        # End-of-season ratings become next season's prior.
        final = season_matches.copy()
        final["weight"] = weights(final)
        prior_att, prior_def, mu_h, mu_a = fit(final, teams, prior_att, prior_def, prior_mu)
        prior_mu = (mu_h, mu_a)
        prev_teams = set(teams)

    return pd.DataFrame(rows)
