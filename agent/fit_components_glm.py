"""Fit and test the component models that can be wired into xPts (D119), and write their seed.

Usage:  python agent/fit_components_glm.py [--out data/agent/glm/d119.md] [--no-seed]

Reads the model-ready terms from int_player_component_features (computed in SQL, so the dbt
model applies exactly these terms) and the live xPts components from fct_player_expected_points.

1. Goals and assists, trimmed GLM: the v1 base terms + threat per 90 (last 5), xGI / (influence +
   creativity), xG / threat, expected goal difference, net transfers. Per outfield position, a
   Poisson regression of the rate (count / (minutes / 90), weight minutes / 90) on standardised
   terms (NULL -> training median), with an L2 penalty picked on 2023/24 -> 2024/25.
   Ship rule (D119): wire it in only if top-11 captured AND rank among likely starters are >= the
   formula's on BOTH the 2025/26 hold-out and the 2026/27 walk-forward. Otherwise the level
   fix: the formula's goals / assists x (actual / formula forecast) per position, fitted on the
   training seasons.
2. Defensive contribution: the D118 NB2 count model per position, then a per-position Platt
   calibration (logit P = a + b logit P_raw) fitted on out-of-fold predictions within the
   training data (two folds by gameweek parity). Train on 2025/26; walk forward through 2026/27.

The seed dbt/seeds/component_glm.csv holds one row per (component, position, term): the
training mean, SD and median of the term and its coefficient; special terms: _intercept,
_alpha (NB dispersion), _cal_a / _cal_b (Platt), _level_scale. Goals / assists / level scales
are fitted on 2023/24 + 2024/25; DC on 2025/26 (training seasons only).
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
from scipy.stats import nbinom
from sklearn.linear_model import PoissonRegressor

import fit_glm as v1

ROOT = v1.ROOT
DB = v1.DB
SEED = ROOT / "dbt" / "seeds" / "component_glm.csv"
POSITIONS = ["DEF", "MID", "FWD"]
GOAL_PTS, ASSIST_PTS = v1.GOAL_PTS, v1.ASSIST_PTS
DC_THRESH = {"DEF": 10, "MID": 12, "FWD": 12}
SCEN = [("p_60", 87), ("p_early", 62), ("p_sub", 22)]
ALPHAS = [1e-6, 1e-4, 1e-3, 1e-2, 3e-2, 0.1, 0.3]
TRAIN = ["2023-24", "2024-25"]
EXTRA = ["threat90_l5", "xgi_per_ic_l10", "xg_per_threat_l10", "exp_goal_diff", "net_transfer_ratio"]
GA = {
    "goals": dict(y="goals_scored", terms=v1.features("goals") + EXTRA),
    "assists": dict(y="assists", terms=v1.features("assists") + EXTRA),
}
DC_TERMS = ["log_dc90_season", "log_dc90_l5", "log_dc90_l3", "log_dc_minutes_season",
            "log_opp_dc_forced", "log_team_dc_made", "log_opp_dc_made", "exp_goal_diff", "is_home",
            "gap_x_home", "log_team_xg_for_l5", "log_opp_xg_for_l5", "role_min_delta",
            "role_start_delta", "rest_days", "matches_l14"]


def load():
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select c.*, e.xpts, e.xp_goals, e.xp_assists, e.xp_defensive_contribution
        from main.int_player_component_features c
        join main.fct_player_expected_points e using (season, player_key, fixture_id)
        where not c.is_upcoming and c.minutes is not null""").df()
    con.close()
    df["em"] = 87 * df.p_60 + 62 * df.p_early + 22 * df.p_sub
    df["goal_pts"] = df.position_code.map(GOAL_PTS).fillna(0)
    df["f_goals"] = np.where(df.goal_pts > 0, df.xp_goals / df.goal_pts.replace(0, np.nan), 0)
    df["f_assists"] = df.xp_assists / ASSIST_PTS
    # The formula's rate per 90 = forecast / (expected minutes / 90).
    for t in ("goals", "assists"):
        df[f"f_{t}_rate"] = df[f"f_{t}"] / (df.em / 90).replace(0, np.nan)
        df[f"f_{t}_rate"] = df[f"f_{t}_rate"].fillna(0)
    df["season_gw"] = df.season + "-" + df.gameweek.astype(str).str.zfill(2)
    df["defensive_contribution"] = df.defensive_contribution.fillna(0)
    return df


class Lin:
    """Standardised linear predictor with training-median fill; Poisson (sklearn) or NB2."""

    def __init__(self, terms, family="poisson", alpha=0.0):
        self.terms, self.family, self.alpha_pen = terms, family, alpha

    def fit(self, d, y):
        X = d[self.terms]
        self.med = X.median()
        X = X.fillna(self.med)
        self.mu, self.sd = X.mean(), X.std().replace(0, 1)
        Z = (X - self.mu) / self.sd
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if self.family == "poisson":
                m = PoissonRegressor(alpha=self.alpha_pen, max_iter=2000)
                m.fit(Z, d[y] / (d.minutes / 90), sample_weight=d.minutes / 90)
                self.b0, self.coef, self.nb_alpha = m.intercept_, m.coef_, 0.0
            else:
                m = sm.NegativeBinomial(d[y], sm.add_constant(Z), exposure=d.minutes / 90).fit(disp=0, maxiter=300)
                p = m.params.values
                self.b0, self.coef, self.nb_alpha = p[0], p[1:-1], p[-1]
        return self

    def rate(self, rows):
        Z = (rows[self.terms].fillna(self.med) - self.mu) / self.sd
        return np.exp(self.b0 + Z.values @ self.coef)

    def p_hit(self, rows, thr):
        r = self.rate(rows)
        n = 1 / self.nb_alpha
        p = np.zeros(len(rows))
        for col, mins in SCEN:
            mu = np.clip(r * mins / 90, 1e-9, None)
            p += rows[col].values * nbinom.sf(thr - 1, n, n / (n + mu))
        return p

    def seed_rows(self, component, pos, fitted_on):
        rows = [dict(component=component, position_code=pos, term=t, mu=self.mu[t], sd=self.sd[t],
                     median=self.med[t], coef=c, fitted_on=fitted_on) for t, c in zip(self.terms, self.coef)]
        rows.append(dict(component=component, position_code=pos, term="_intercept", coef=self.b0, fitted_on=fitted_on))
        if self.family == "nb":
            rows.append(dict(component=component, position_code=pos, term="_alpha", coef=self.nb_alpha, fitted_on=fitted_on))
        return rows


def played(d, pos):
    return d[(d.position_code == pos) & (d.minutes > 0)]


# ----------------------------------------------------------------------------- goals / assists
def pick_alpha(df):
    tr, va = df[df.season == "2023-24"], df[df.season == "2024-25"]
    out = {}
    for t, spec in GA.items():
        for pos in POSITIONS:
            v = played(va, pos)
            devs = {a: v1.deviance(v[spec["y"]].values,
                                   Lin(spec["terms"], alpha=a).fit(played(tr, pos), spec["y"]).rate(v) * v.minutes.values / 90)
                    for a in ALPHAS}
            out[(t, pos)] = min(devs, key=devs.get)
    return out


def ga_models(train, alphas):
    return {(t, pos): Lin(spec["terms"], alpha=alphas[(t, pos)]).fit(played(train, pos), spec["y"])
            for t, spec in GA.items() for pos in POSITIONS}


def level_scales(train):
    o = train[train.position_code.isin(POSITIONS)]
    return {(t, pos): o.loc[o.position_code == pos, GA[t]["y"]].sum() / o.loc[o.position_code == pos, f"f_{t}"].sum()
            for t in GA for pos in POSITIONS}


def ga_predict(train, test, alphas):
    test = test.copy()
    ms, sc = ga_models(train, alphas), level_scales(train)
    for t in GA:
        test[f"trim_{t}_rate"] = np.nan
        test[f"level_{t}_rate"] = np.nan
        for pos in POSITIONS:
            mask = test.position_code == pos
            test.loc[mask, f"trim_{t}_rate"] = ms[(t, pos)].rate(test[mask])
            test.loc[mask, f"level_{t}_rate"] = test.loc[mask, f"f_{t}_rate"] * sc[(t, pos)]
    return test


# ----------------------------------------------------------------------------- DC
def dc_fit(train):
    """NB per position + Platt calibration on out-of-fold predictions (folds = GW parity)."""
    out = {}
    for pos in POSITIONS:
        d = train[train.position_code == pos]
        oof = pd.Series(np.nan, index=d.index)
        for k in (0, 1):
            fold = d.gameweek % 2 == k
            m = Lin(DC_TERMS, "nb").fit(played(d[~fold], pos), "defensive_contribution")
            oof[fold] = m.p_hit(d[fold], DC_THRESH[pos])
        y = (d.defensive_contribution >= DC_THRESH[pos]).astype(int)
        lo = np.log(np.clip(oof, 1e-6, 1 - 1e-6) / (1 - np.clip(oof, 1e-6, 1 - 1e-6)))
        cal = sm.Logit(y, sm.add_constant(lo.values)).fit(disp=0).params
        out[pos] = (Lin(DC_TERMS, "nb").fit(played(d, pos), "defensive_contribution"), cal[0], cal[1])
    return out


def dc_apply(models, rows):
    p_raw = np.full(len(rows), np.nan)
    p_cal = np.full(len(rows), np.nan)
    for pos, (m, a, b) in models.items():
        mask = (rows.position_code == pos).values
        if mask.any():
            pr = m.p_hit(rows[mask], DC_THRESH[pos])
            pc = np.clip(pr, 1e-6, 1 - 1e-6)
            p_raw[mask] = pr
            p_cal[mask] = 1 / (1 + np.exp(-(a + b * np.log(pc / (1 - pc)))))
    return p_raw, p_cal


def dc_metrics(d, col):
    y, p = d.hit.values, np.clip(d[col].values, 1e-6, 1 - 1e-6)
    s = d[d.p_start >= 0.5]
    return {"log_loss": -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)), "brier": np.mean((p - y) ** 2),
            "pred_hit_rate": p.mean(), "actual_hit_rate": y.mean(), "dc_xpts_bias": np.mean(2 * (p - y)),
            "rank_starters": s.groupby("season_gw").apply(lambda g: g[col].rank().corr(g.hit.rank()),
                                                          include_groups=False).mean()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "agent" / "glm" / "d119.md"))
    ap.add_argument("--no-seed", action="store_true")
    a = ap.parse_args()
    df = load()
    alphas = pick_alpha(df)
    print("alphas", alphas, flush=True)
    train = df[df.season.isin(TRAIN)]
    names = ["formula", "trim", "level"]

    hold = ga_predict(train, df[df.season == "2025-26"], alphas)
    hold_m = v1.evaluate(hold, names)
    cur = df[df.season == "2026-27"]
    wf = pd.concat([ga_predict(df[(df.season < "2026-27") | ((df.season == "2026-27") & (df.gameweek < gw))],
                               cur[cur.gameweek == gw], alphas) for gw in sorted(cur.gameweek.unique())])
    wf_m = v1.evaluate(wf, names)
    print(hold_m.round(3).T, "\n", wf_m.round(3).T, flush=True)
    ok = all(m.loc["trim", k] >= m.loc["formula", k]
             for m in (hold_m, wf_m) for k in ("captured_top11", "xpts_rank_starters"))
    print("ship trimmed GLM:", ok, flush=True)

    # DC: train 2025/26, walk forward through 2026/27.
    dc = df[df.season.isin(["2025-26", "2026-27"]) & df.position_code.isin(POSITIONS)].copy()
    dc["hit"] = (dc.defensive_contribution >= dc.position_code.map(DC_THRESH)).astype(int)
    dc["p_formula"] = dc.xp_defensive_contribution / 2
    dcur = dc[dc.season == "2026-27"]
    parts = []
    for gw in sorted(dcur.gameweek.unique()):
        tr = dc[(dc.season == "2025-26") | (dc.gameweek < gw)]
        te = dcur[dcur.gameweek == gw].copy()
        te["p_nb"], te["p_nb_cal"] = dc_apply(dc_fit(tr), te)
        parts.append(te)
    dwf = pd.concat(parts)
    # The static seed's view: trained on 2025/26 only.
    static = dc_fit(dc[dc.season == "2025-26"])
    dwf["p_nb_cal_static"] = dc_apply(static, dwf)[1]
    rows = []
    for pos in POSITIONS + ["DEF+MID"]:
        d = dwf[dwf.position_code.isin(["DEF", "MID"])] if pos == "DEF+MID" else dwf[dwf.position_code == pos]
        for col, nm in (("p_formula", "formula (live)"), ("p_nb", "NB"), ("p_nb_cal", "NB + calibration"),
                        ("p_nb_cal_static", "NB + calibration, fit on 2025/26 only (= seed)")):
            rows.append({"position": pos, "model": nm, **dc_metrics(d, col)})
    dc_res = pd.DataFrame(rows).set_index(["position", "model"])
    print(dc_res.round(4).to_string(), flush=True)

    if not a.no_seed:
        seed = []
        ms = ga_models(train, alphas)
        for (t, pos), m in ms.items():
            seed += m.seed_rows(t, pos, "2023-24+2024-25")
        for (t, pos), s in level_scales(train).items():
            seed.append(dict(component=t, position_code=pos, term="_level_scale", coef=s, fitted_on="2023-24+2024-25"))
        for pos, (m, ca, cb) in static.items():
            seed += m.seed_rows("dc", pos, "2025-26")
            seed += [dict(component="dc", position_code=pos, term="_cal_a", coef=ca, fitted_on="2025-26"),
                     dict(component="dc", position_code=pos, term="_cal_b", coef=cb, fitted_on="2025-26")]
        sd = pd.DataFrame(seed)[["component", "position_code", "term", "mu", "sd", "median", "coef", "fitted_on"]]
        sd.to_csv(SEED, index=False, float_format="%.10g", lineterminator="\n")
        print("wrote", SEED, len(sd), "rows")
        # Save the static seed-model predictions for the dbt parity check.
        chk = df[df.position_code.isin(POSITIONS)].copy()
        for t in GA:
            for pos in POSITIONS:
                mask = chk.position_code == pos
                chk.loc[mask, f"{t}_rate"] = ms[(t, pos)].rate(chk[mask])
        chk["dc_p_hit"] = dc_apply(static, chk)[1]
        chk[["season", "player_key", "fixture_id", "goals_rate", "assists_rate", "dc_p_hit"]].to_csv(
            ROOT / "data" / "agent" / "glm" / "seed_predictions.csv", index=False)

    text = ["# D119: trimmed goals/assists GLM, level fix, calibrated DC model", "", __doc__.strip(), "",
            "Penalty per (target, position): " + ", ".join(f"{t}/{p}={v:g}" for (t, p), v in alphas.items()), "",
            "## Goals + assists, 2025/26 hold-out (trained on 2023/24 + 2024/25)", "",
            "trim = trimmed GLM; level = formula x per-position level scale.", "", v1.md(hold_m.T), "",
            "## Goals + assists, 2026/27 walk-forward (refit before each GW)", "", v1.md(wf_m.T), "",
            f"Ship rule (top-11 captured and starters' rank >= formula on both): **{'PASS' if ok else 'FAIL'}**", "",
            "## DC, 2026/27 walk-forward", "", v1.md(dc_res, floatfmt=".4f"), ""]
    pathlib.Path(a.out).write_text("\n".join(text), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
