"""Per-component regressions for xPts, v1: goals and assists (D116).

Usage:  python agent/fit_glm.py [--out data/agent/glm/goals_assists_v1.md]

Data: int_player_component_features (pre-match predictors, player x fixture, all seasons).
Model: for each outfield position (DEF, MID, FWD) and each target (goals, assists), a Poisson
GLM of the count on the standardised predictors with offset log(minutes / 90), fitted on
matches he played. So it predicts a scoring RATE; the forecast is rate x expected minutes from
the existing minutes model (87 P(60+) + 62 P(early sub) + 22 P(sub)), the same minutes the
formula uses, so the comparison is about the rate only. Keepers keep the formula.
Challenger: sklearn HistGradientBoosting with a Poisson loss on the rate (sample weight =
minutes / 90), one model per target with position as a feature.

Splits: train 2023/24 + 2024/25, score the 2025/26 hold-out; then 2026/27 walk-forward, refit
before every gameweek on everything earlier.

Metrics (D94: a ranking tool): mean Poisson deviance of the forecast (all outfield rows, and
given actual minutes); within-GW Spearman of goal + assist points among likely starters
(P(start) >= 0.5); and with the formula's goal and assist points swapped for the model's in
raw xPts, the tune_xpts metrics: top-11 points captured, top-20 average points, within-GW rank.
"""
import argparse
import os
import pathlib
import sys
import warnings

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
GOAL_PTS = {"DEF": 6, "MID": 5, "FWD": 4}
ASSIST_PTS = 3
POSITIONS = ["DEF", "MID", "FWD"]
EPS = 0.01

TARGETS = {
    "goals": dict(y="goals_scored", own="xg", gap="g_minus_xg", formula="xp_goals"),
    "assists": dict(y="assists", own="xa", gap="a_minus_xa", formula="xp_assists"),
}


def load():
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select c.*, e.xpts, e.xp_goals, e.xp_assists, e.xp_defensive_contribution, e.xpts_calibrated
        from main.int_player_component_features c
        join main.fct_player_expected_points e using (season, player_key, fixture_id)
        where not c.is_upcoming and c.minutes is not null""").df()
    con.close()
    df["em"] = 87 * df.p_60 + 62 * df.p_early + 22 * df.p_sub
    df["goal_pts"] = df.position_code.map(GOAL_PTS).fillna(0)
    # Formula forecasts as counts (component scales are off, so xp = count x points).
    df["f_goals"] = np.where(df.goal_pts > 0, df.xp_goals / df.goal_pts.replace(0, np.nan), 0)
    df["f_assists"] = df.xp_assists / ASSIST_PTS
    # Formula rates per 90 (for the given-minutes comparison).
    df["f_goals_rate"] = df.xg_per90_form * df.attack_factor.fillna(1)
    df["f_assists_rate"] = df.xa_per90_form * df.attack_factor.fillna(1)
    for m in ("xg", "xa"):
        s = df[f"{m}_per90_season"]
        df[f"log_{m}90_season"] = np.log(s + EPS)
        for n in (3, 5):
            # Window rate shrunk towards his season rate with a 90-minute pseudo-count.
            rate = (df[f"{m}_l{n}"] + s) / (df[f"minutes_l{n}"] / 90 + 1)
            df[f"log_{m}90_l{n}"] = np.log(rate + EPS)
    # Promoted teams' first match has no team form: fall back to the fixture expectation.
    df["team_xg_for_l5"] = df.team_xg_for_l5.fillna(df.expected_goals_for)
    df["team_xg_against_l5"] = df.team_xg_against_l5.fillna(df.expected_goals_against)
    df["opp_xg_for_l5"] = df.opp_xg_for_l5.fillna(df.expected_goals_against)
    df["opp_xg_against_l5"] = df.opp_xg_against_l5.fillna(df.expected_goals_for)
    df["log_exp_goals_for"] = np.log(df.expected_goals_for)
    df["log_team_xg_for_l5"] = np.log(df.team_xg_for_l5)
    df["log_opp_xg_against_l5"] = np.log(df.opp_xg_against_l5)
    df["log_minutes_prior"] = np.log1p(df.minutes_prior / 90)
    df["season_gw"] = df.season + "-" + df.gameweek.astype(str).str.zfill(2)
    return df


def features(t):
    own, gap = TARGETS[t]["own"], TARGETS[t]["gap"]
    return [f"log_{own}90_season", f"log_{own}90_l5", f"log_{own}90_l3",
            f"{gap}_l5", f"{gap}_l10", f"{gap}_trend",
            "log_minutes_prior",
            "log_exp_goals_for", "log_team_xg_for_l5", "log_opp_xg_against_l5",
            "is_home", "league_position", "opp_league_position", "points_gap", "rest_days",
            "own_top3_p_start", "opp_top3_p_start"]


def fit_glm(train, t, pos):
    cols = features(t)
    d = train[(train.position_code == pos) & (train.minutes > 0)]
    mu, sd = d[cols].mean(), d[cols].std().replace(0, 1)
    X = sm.add_constant((d[cols] - mu) / sd)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = sm.GLM(d[TARGETS[t]["y"]], X, family=sm.families.Poisson(),
                   offset=np.log(d.minutes / 90)).fit()
    return m, mu, sd


def glm_rate(model, mu, sd, rows, t):
    X = sm.add_constant((rows[features(t)] - mu) / sd, has_constant="add")
    return np.exp(X.values @ model.params.values)


def fit_gbm(train, t):
    cols = features(t) + ["pos_id"]
    d = train[(train.position_code.isin(POSITIONS)) & (train.minutes > 0)]
    g = HistGradientBoostingRegressor(loss="poisson", learning_rate=0.05, max_iter=300,
                                      max_leaf_nodes=15, min_samples_leaf=200,
                                      l2_regularization=1.0, random_state=0)
    g.fit(d[cols], d[TARGETS[t]["y"]] / (d.minutes / 90), sample_weight=d.minutes / 90)
    return g


def predict(train, test):
    """Rates per 90 for every outfield row of test, GLM and GBM, fitted on train."""
    out = test.copy()
    for t in TARGETS:
        out[f"glm_{t}_rate"] = np.nan
        for pos in POSITIONS:
            m, mu, sd = fit_glm(train, t, pos)
            mask = out.position_code == pos
            out.loc[mask, f"glm_{t}_rate"] = glm_rate(m, mu, sd, out[mask], t)
        g = fit_gbm(train, t)
        mask = out.position_code.isin(POSITIONS)
        out.loc[mask, f"gbm_{t}_rate"] = g.predict(out.loc[mask, features(t) + ["pos_id"]])
    return out


def deviance(y, mu):
    mu = np.clip(mu, 1e-9, None)
    return float(np.mean(2 * (np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0) - (y - mu))))


def spearman_by_gw(d, pred, actual):
    return float(d.groupby("season_gw").apply(lambda g: g[pred].rank().corr(g[actual].rank()),
                                             include_groups=False).mean())


def evaluate(df, names=("formula", "glm", "gbm")):
    """df: scored rows with <name>_<target>_rate columns. Returns a metrics table (one row per model)."""
    rows = []
    out = df[df.position_code.isin(POSITIONS)].copy()
    out["actual_ga_pts"] = out.goals_scored * out.goal_pts + out.assists * ASSIST_PTS
    starters = out.p_start >= 0.5
    played = out.minutes > 0
    for name in names:
        r = {"model": name}
        for t in TARGETS:
            y = out[TARGETS[t]["y"]].values
            if name == "formula":
                fc, rate = out[f"f_{t}"].values, out[f"f_{t}_rate"].values
            else:
                rate = out[f"{name}_{t}_rate"].values
                fc = rate * out.em.values / 90
            out[f"{name}_{t}_fc"] = fc
            r[f"{t}_dev"] = deviance(y, fc)
            r[f"{t}_dev_given_min"] = deviance(y[played], (rate * out.minutes.values / 90)[played])
            r[f"{t}_pred_vs_actual"] = fc.sum() / y.sum()
        out[f"{name}_ga_pts"] = out[f"{name}_goals_fc"] * out.goal_pts + out[f"{name}_assists_fc"] * ASSIST_PTS
        r["ga_rank_starters"] = spearman_by_gw(out[starters], f"{name}_ga_pts", "actual_ga_pts")
        rows.append(r)
    # Swap into full raw xPts (keepers unchanged) and score like tune_xpts.
    full = df.copy()
    for name in names:
        col = f"xpts_{name}"
        full[col] = full.xpts
        if name != "formula":
            m = full.position_code.isin(POSITIONS)
            full.loc[m, col] = (full.xpts - full.xp_goals - full.xp_assists)[m] + \
                out[f"{name}_ga_pts"].reindex(full.index)[m]
    for r in rows:
        col = f"xpts_{r['model']}"
        g = full.groupby("season_gw")
        r["captured_top11"] = float(g.apply(
            lambda x: x.nlargest(11, col).total_points.sum() / x.nlargest(11, "total_points").total_points.sum(),
            include_groups=False).mean())
        r["top20_avg_points"] = float(g.apply(lambda x: x.nlargest(20, col).total_points.mean(),
                                              include_groups=False).mean())
        r["xpts_rank_all"] = spearman_by_gw(full, col, "total_points")
        r["xpts_rank_starters"] = spearman_by_gw(full[full.p_start >= 0.5], col, "total_points")
    return pd.DataFrame(rows).set_index("model")


def coef_table(train):
    rows = []
    for t in TARGETS:
        for pos in POSITIONS:
            m, _, _ = fit_glm(train, t, pos)
            for k in m.params.index:
                rows.append({"target": t, "pos": pos, "term": k, "coef_per_sd": m.params[k],
                             "se": m.bse[k], "z": m.params[k] / m.bse[k],
                             "rate_ratio_per_sd": np.exp(m.params[k])})
    return pd.DataFrame(rows)


def md(df, floatfmt=".3f"):
    return df.to_markdown(floatfmt=floatfmt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "agent" / "glm" / "goals_assists_v1.md"))
    a = ap.parse_args()
    df = load()
    df["pos_id"] = df.position_code.map({"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3})

    train = df[df.season.isin(["2023-24", "2024-25"])]
    hold = predict(train, df[df.season == "2025-26"])
    hold_metrics = evaluate(hold)
    print("2025/26 hold-out\n", hold_metrics.round(3).T, flush=True)

    cur = df[df.season == "2026-27"]
    parts = []
    for gw in sorted(cur.gameweek.unique()):
        tr = df[(df.season < "2026-27") | ((df.season == "2026-27") & (df.gameweek < gw))]
        parts.append(predict(tr, cur[cur.gameweek == gw]))
        print(f"walk-forward GW{gw} done", flush=True)
    wf = pd.concat(parts)
    wf_metrics = evaluate(wf)
    print("2026/27 walk-forward\n", wf_metrics.round(3).T, flush=True)
    wf_early = evaluate(wf[wf.gameweek <= 5])

    coefs_hold = coef_table(train)
    coefs_all = coef_table(df[(df.season < "2026-27") | (df.gameweek < cur.gameweek.max())])

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    key_terms = [t for t in coefs_hold.term.unique() if "minus" in t]
    gaps = (coefs_hold[coefs_hold.term.isin(key_terms)]
            .merge(coefs_all[coefs_all.term.isin(key_terms)], on=["target", "pos", "term"],
                   suffixes=("_train2324_2425", "_all_data")))
    text = [
        "# D116 v1: goals and assists regressions",
        "",
        __doc__.strip(),
        "",
        f"Rows: {len(df):,} played-or-benched player-fixtures; GLMs per position fitted on matches played.",
        "",
        "## 2025/26 hold-out (trained on 2023/24 + 2024/25)",
        "",
        "Deviance: lower is better. pred_vs_actual: forecast total / actual total (level).",
        "",
        md(hold_metrics.T),
        "",
        "## 2026/27 walk-forward (refit before each GW on everything earlier)",
        "",
        md(wf_metrics.T),
        "",
        "### 2026/27 GW1-5 only (early season)",
        "",
        md(wf_early.T),
        "",
        "## Over-performance and trend terms (coefficient per SD of the predictor, log rate)",
        "",
        "rate ratio per SD = exp(coef). z beyond +-2 is a reliable effect.",
        "",
        md(gaps[["target", "pos", "term", "coef_per_sd_train2324_2425", "z_train2324_2425",
                 "coef_per_sd_all_data", "z_all_data"]].set_index(["target", "pos", "term"])),
        "",
        "## All GLM coefficients (trained on 2023/24 + 2024/25)",
        "",
        md(coefs_hold.set_index(["target", "pos", "term"])[["coef_per_sd", "se", "z", "rate_ratio_per_sd"]]),
        "",
    ]
    out.write_text("\n".join(text), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
