"""Per-component regressions, v2: goals and assists with the D117 derived features + ablation.

Usage:  python agent/fit_glm_v2.py [--out data/agent/glm/goals_assists_v2.md]

Same data, splits, minutes handling and metrics as fit_glm.py (v1). Differences:
  * ~45 predictors in blocks (base = v1; player; form; team; context; crowd; availability;
    probe = FPL's own expected points), built from int_player_component_features.
  * The GLM is an L2-regularised Poisson regression (sklearn PoissonRegressor) on the
    standardised predictors, fitted on the rate (count / (minutes / 90)) with weight
    minutes / 90 (equivalent to the offset model). The penalty per position and target is
    picked by training on 2023/24 and scoring 2024/25, then the model is refitted on both.
  * The challenger is the same HistGradientBoosting Poisson model on the v2 predictors.
  * Ablation on the 2025/26 hold-out: drop each block, and each non-base feature, refit, and
    report the change in mean Poisson deviance (positive = the feature helps).
  * The probe (FPL xP) is reported separately: the archive's xP looks leaky (see the report),
    and it doesn't exist for 2026/27, so every headline model is fitted without it.
"""
import argparse
import pathlib
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import PoissonRegressor

import fit_glm as v1

ROOT = v1.ROOT
POSITIONS = v1.POSITIONS
TARGETS = v1.TARGETS
EPS = v1.EPS
ALPHAS = [1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1.0]


def slog(x):
    return np.sign(x) * np.log1p(np.abs(x))


def add_v2(df):
    d = df
    s_xgi = d.xg_per90_season + d.xa_per90_season
    d["threat90_l5"] = d.threat_l5 / (d.minutes_l5 / 90 + 1)
    d["xg_share_l5"] = d.xg_l5 / (d.team_xg_for_l5 * d.team_matches_l5.fillna(5).clip(upper=5) + 0.1)
    d["xgi_per_ic_l10"] = (d.xg_l10 + d.xa_l10) / (d.ic_l10 / 100 + 0.5)
    d["xg_per_threat_l10"] = d.xg_l10 / (d.threat_l10 / 100 + 0.5)
    d["bps_ex90_l10"] = d.bps_ex_l10 / (d.minutes_l10 / 90 + 1)
    d["role_min_delta"] = d.minutes_l3 / 3 - d.minutes_prev10 / 10
    d["role_start_delta"] = d.starts_l3 / 3 - d.starts_prev10 / 10
    d["role_xgi_delta"] = (d.xg_l3 + d.xa_l3) / 3 - d.xgi_prev10 / 10
    d["xgi_sd_l10"] = d.xgi_sd_l10.fillna(0)
    d["g_momentum"] = d.g_minus_xg_l3 / 3 - d.g_minus_xg_l10 / 10
    d["a_momentum"] = d.a_minus_xa_l3 / 3 - d.a_minus_xa_l10 / 10
    rate_l5 = (d.xg_l5 + d.xa_l5 + s_xgi) / (d.minutes_l5 / 90 + 1)
    d["log_xgi_form_ratio"] = np.log(rate_l5 + EPS) - np.log(s_xgi + EPS)
    d["exp_goal_diff"] = d.expected_goals_for - d.expected_goals_against
    d["log_opp_leak"] = np.log(d.opp_xgi_conceded_to_pos_l10.fillna(d.opp_xgi_conceded_to_pos_l10.median()) + 0.05)
    d["team_def_xg_share_l10"] = d.team_def_xg_share_l10.fillna(d.team_def_xg_share_l10.median())
    d["team_overperf_l5"] = (d.team_goals_for_l5 - d.team_xg_for_l5).fillna(0)
    d["team_xgf_shift"] = (d.team_xg_for_l5 - d.team_xg_for_6_15).fillna(0)
    d["team_xga_shift"] = (d.team_xg_against_l5 - d.team_xg_against_6_15).fillna(0)
    d["opp_xga_shift"] = (d.opp_xg_against_l5 - d.opp_xg_against_6_15).fillna(0)
    d["gap_x_home"] = d.exp_goal_diff * (2 * d.is_home - 1)
    d["net_transfer_ratio"] = slog(d.net_transfers_l2 / np.maximum(d.selected_l1.fillna(0), 1000))
    d["price_velocity"] = (d.price_l1 - d.price_l4).fillna(0)
    d["log_fpl_xp"] = np.log(d.fpl_xp.clip(lower=0) + 0.1)
    d["opp_back_four_missing"] = 1 - d.opp_back_four_p_start.fillna(d.opp_back_four_p_start.median())
    d["opp_gk_gc_minus_xgc_l10"] = d.opp_gk_gc_minus_xgc_l10.fillna(0)
    return d


def blocks(t):
    gap = "g" if t == "goals" else "a"
    return {
        "base (v1)": v1.features(t),
        "player": ["threat90_l5", "xg_share_l5", "xgi_per_ic_l10", "xg_per_threat_l10", "bps_ex90_l10",
                   "role_min_delta", "role_start_delta", "role_xgi_delta", "xgi_sd_l10"],
        "form": [f"{gap}_momentum", "matches_since_return", "return_streak", "log_xgi_form_ratio"],
        "team": ["exp_goal_diff", "log_opp_leak", "team_def_xg_share_l10", "team_overperf_l5",
                 "team_xgf_shift", "team_xga_shift", "opp_xga_shift"],
        "context": ["matches_l14", "points_above_17th", "points_below_5th", "gameweek", "gap_x_home"],
        "crowd": ["net_transfer_ratio", "price_velocity"],
        "availability": ["opp_back_four_missing", "opp_gk_gc_minus_xgc_l10"],
    }


def feats(t, drop=(), probe=False):
    cols = [c for b in blocks(t).values() for c in b if c not in drop]
    return cols + (["log_fpl_xp"] if probe else [])


class Ridge:
    """Standardise, fill NaN with the training median, PoissonRegressor on the rate."""

    def __init__(self, cols, alpha):
        self.cols, self.alpha = cols, alpha

    def fit(self, d, y):
        X = d[self.cols]
        self.med = X.median()
        X = X.fillna(self.med)
        self.mu, self.sd = X.mean(), X.std().replace(0, 1)
        self.m = PoissonRegressor(alpha=self.alpha, max_iter=1000)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.m.fit((X - self.mu) / self.sd, d[y] / (d.minutes / 90), sample_weight=d.minutes / 90)
        return self

    def rate(self, rows):
        X = (rows[self.cols].fillna(self.med) - self.mu) / self.sd
        return self.m.predict(X)


def played(d, pos):
    return d[(d.position_code == pos) & (d.minutes > 0)]


def pick_alphas(df):
    """Penalty per (target, position): train 2023/24, score 2024/25 (deviance given minutes)."""
    tr, va = df[df.season == "2023-24"], df[df.season == "2024-25"]
    out = {}
    for t in TARGETS:
        y = TARGETS[t]["y"]
        for pos in POSITIONS:
            best = None
            v = played(va, pos)
            for a in ALPHAS:
                r = Ridge(feats(t), a).fit(played(tr, pos), y).rate(v)
                dev = v1.deviance(v[y].values, r * v.minutes.values / 90)
                if best is None or dev < best[1]:
                    best = (a, dev)
            out[(t, pos)] = best[0]
    return out


def ridge_predict(train, test, alphas, name, drop=(), probe=False, targets=TARGETS):
    for t in targets:
        y = TARGETS[t]["y"]
        test[f"{name}_{t}_rate"] = np.nan
        for pos in POSITIONS:
            m = Ridge(feats(t, drop, probe), alphas[(t, pos)]).fit(played(train, pos), y)
            mask = test.position_code == pos
            test.loc[mask, f"{name}_{t}_rate"] = m.rate(test[mask])
    return test


def gbm_predict(train, test, name):
    for t in TARGETS:
        y = TARGETS[t]["y"]
        cols = feats(t) + ["pos_id"]
        d = train[train.position_code.isin(POSITIONS) & (train.minutes > 0)]
        g = HistGradientBoostingRegressor(loss="poisson", learning_rate=0.05, max_iter=300,
                                          max_leaf_nodes=15, min_samples_leaf=200,
                                          l2_regularization=1.0, random_state=0)
        g.fit(d[cols], d[y] / (d.minutes / 90), sample_weight=d.minutes / 90)
        mask = test.position_code.isin(POSITIONS)
        test.loc[mask, f"{name}_{t}_rate"] = g.predict(test.loc[mask, cols])
    return test


def v1_glm_predict(train, test):
    for t in TARGETS:
        test[f"glm_v1_{t}_rate"] = np.nan
        for pos in POSITIONS:
            m, mu, sd = v1.fit_glm(train, t, pos)
            mask = test.position_code == pos
            test.loc[mask, f"glm_v1_{t}_rate"] = v1.glm_rate(m, mu, sd, test[mask], t)
    return test


def score_all(train, test, alphas, probe_ok):
    test = test.copy()
    test = v1_glm_predict(train, test)
    test = ridge_predict(train, test, alphas, "v2")
    test = gbm_predict(train, test, "gbm_v2")
    names = ["formula", "glm_v1", "v2", "gbm_v2"]
    if probe_ok:
        test = ridge_predict(train, test, alphas, "v2_probe", probe=True)
        names.append("v2_probe")
    return test, names


def outfield_dev(test, name, t):
    o = test[test.position_code.isin(POSITIONS)]
    y = o[TARGETS[t]["y"]].values
    return v1.deviance(y, o[f"{name}_{t}_rate"].values * o.em.values / 90)


def ablation(train, test, alphas):
    base = ridge_predict(train, test.copy(), alphas, "full")
    full = {t: outfield_dev(base, "full", t) for t in TARGETS}
    rows = []
    for t in TARGETS:
        units = [(f"[block] {b}", cols) for b, cols in blocks(t).items()] + \
                [(c, [c]) for b, cols in blocks(t).items() if b != "base (v1)" for c in cols]
        for label, drop in units:
            d = ridge_predict(train, test.copy(), alphas, "abl", drop=drop, targets=[t])
            rows.append({"target": t, "dropped": label,
                         "deviance_change_x1000": (outfield_dev(d, "abl", t) - full[t]) * 1000})
        print(f"ablation {t} done", flush=True)
    return pd.DataFrame(rows), full


def coef_table(train, alphas, probe=False):
    rows = []
    for t in TARGETS:
        for pos in POSITIONS:
            m = Ridge(feats(t, probe=probe), alphas[(t, pos)]).fit(played(train, pos), TARGETS[t]["y"])
            for c, b in zip(m.cols, m.m.coef_):
                rows.append({"target": t, "pos": pos, "term": c, "coef_per_sd": b,
                             "rate_ratio_per_sd": np.exp(b)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "agent" / "glm" / "goals_assists_v2.md"))
    a = ap.parse_args()
    df = add_v2(v1.load())
    df["pos_id"] = df.position_code.map({"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3})

    alphas = pick_alphas(df)
    print("alphas", alphas, flush=True)
    train = df[df.season.isin(["2023-24", "2024-25"])]
    hold, names = score_all(train, df[df.season == "2025-26"], alphas, probe_ok=True)
    hold_m = v1.evaluate(hold, names)
    print(hold_m.round(3).T, flush=True)

    cur = df[df.season == "2026-27"]
    parts = []
    for gw in sorted(cur.gameweek.unique()):
        tr = df[(df.season < "2026-27") | ((df.season == "2026-27") & (df.gameweek < gw))]
        parts.append(score_all(tr, cur[cur.gameweek == gw], alphas, probe_ok=False)[0])
        print(f"walk-forward GW{gw}", flush=True)
    wf = pd.concat(parts)
    wf_names = ["formula", "glm_v1", "v2", "gbm_v2"]
    wf_m = v1.evaluate(wf, wf_names)
    print(wf_m.round(3).T, flush=True)

    abl, full = ablation(train, df[df.season == "2025-26"], alphas)
    coefs = coef_table(train, alphas)
    probe_coefs = coef_table(train, alphas, probe=True)
    probe_coefs = probe_coefs[probe_coefs.term == "log_fpl_xp"]

    piv = abl.pivot(index="dropped", columns="target", values="deviance_change_x1000")
    piv["sum"] = piv.sum(axis=1)
    piv = piv.sort_values("sum", ascending=False)
    key = coefs[coefs.term.str.contains("minus|momentum|trend|return|streak|form_ratio")]
    out = pathlib.Path(a.out)
    text = [
        "# D117 v2: goals and assists with derived features, plus ablation", "",
        __doc__.strip(), "",
        "Penalty (alpha) per target and position, picked on 2024/25: "
        + ", ".join(f"{t}/{p}={v:g}" for (t, p), v in alphas.items()), "",
        "## 2025/26 hold-out (trained on 2023/24 + 2024/25)", "",
        "formula = current xPts; glm_v1 = v1 GLM; v2 = regularised GLM, v2 features, no probe; "
        "gbm_v2 = boosting on v2 features; v2_probe = v2 + FPL xP (see the caveat).", "",
        v1.md(hold_m.T), "",
        "## 2026/27 walk-forward (refit before each GW; no probe: FPL xP is missing this season)", "",
        v1.md(wf_m.T), "",
        "## Ablation on the 2025/26 hold-out (v2 GLM, no probe)", "",
        f"Full-model mean deviance: goals {full['goals']:.5f}, assists {full['assists']:.5f}. "
        "Change x1000 when the block / feature is dropped and the model refitted: positive = it "
        "helps, negative = the model is better without it. For scale, v1 GLM vs the formula "
        "was about -3 (goals) and -4 (assists) x1000.", "",
        v1.md(piv, floatfmt=".2f"), "",
        "## Form terms in the v2 GLM (per SD, log rate; regularised, so no SEs)", "",
        v1.md(key.set_index(["target", "pos", "term"])), "",
        "## Probe: weight on FPL xP when added (per SD, log rate)", "",
        v1.md(probe_coefs.set_index(["target", "pos", "term"])), "",
        "## All v2 GLM coefficients (trained on 2023/24 + 2024/25)", "",
        v1.md(coefs.set_index(["target", "pos", "term"])), "",
    ]
    out.write_text("\n".join(text), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
