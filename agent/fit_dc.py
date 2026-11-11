"""Defensive contribution as a count model (D118).

Usage:  python agent/fit_dc.py [--out data/agent/glm/dc_v1.md]

Target: the DC action count per fixture (DEF: tackles + CBI; MID/FWD: + recoveries; the API's
defensive_contribution, checked equal to that sum). 2 points at 10+ (DEF) or 12+ (MID/FWD).
Model: per position, a negative binomial (NB2) regression of the count with exposure
minutes / 90, fitted on matches played. Prediction: the per-90 rate x each minutes scenario of
the minutes model (87 for 60+, 62 for an early sub, 22 off the bench), P(count >= threshold)
from the fitted NB in each, mixed by the scenario probabilities; xPts = 2 x P(hit).
A Poisson version shows what the dispersion buys.
Data: DC exists from 2025/26 only. Train on 2025/26, then walk forward through 2026/27,
refitting before each GW on everything earlier.
Baseline: the live xp_defensive_contribution (hit rate x (P(60+) + 0.5 P(early sub)) x 2), so
its P(hit) = xp / 2.
"""
import argparse
import pathlib
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import nbinom, poisson

import fit_glm as v1
import fit_glm_v2 as v2

ROOT = v1.ROOT
THRESH = {"DEF": 10, "MID": 12, "FWD": 12}
POSITIONS = ["DEF", "MID", "FWD"]
SCEN = [("p_60", 87), ("p_early", 62), ("p_sub", 22)]
FEATURES = ["log_dc90_season", "log_dc90_l5", "log_dc90_l3", "log_dc_minutes_season",
            "log_opp_dc_forced", "log_team_dc_made", "log_opp_dc_made",
            "exp_goal_diff", "is_home", "gap_x_home", "log_team_xg_for_l5", "log_opp_xg_for_l5",
            "role_min_delta", "role_start_delta", "rest_days", "matches_l14"]


def prepare(df, pos_rate):
    d = df
    m = d.position_code.map(pos_rate)
    d["dc90_season"] = (d.dc_season + 3 * m) / (d.dc_minutes_season / 90 + 3)
    for n in (3, 5, 10):
        d[f"dc90_l{n}"] = (d[f"dc_l{n}"] + d.dc90_season) / (d[f"dc_minutes_l{n}"] / 90 + 1)
    for c in ("dc90_season", "dc90_l5", "dc90_l3"):
        d[f"log_{c}"] = np.log(d[c] + 0.1)
    d["log_dc_minutes_season"] = np.log1p(d.dc_minutes_season / 90)
    d["log_opp_dc_forced"] = np.log(d.opp_dc_forced_to_pos_l5.fillna(d.opp_dc_forced_to_pos_l5.median()) + 1)
    d["log_team_dc_made"] = np.log(d.team_dc_made_l5.fillna(d.team_dc_made_l5.median()) + 1)
    d["log_opp_dc_made"] = np.log(d.opp_dc_made_l5.fillna(d.opp_dc_made_l5.median()) + 1)
    d["log_opp_xg_for_l5"] = np.log(d.opp_xg_for_l5)
    return d


class Count:
    def __init__(self, family):
        self.family = family

    def fit(self, d):
        X = d[FEATURES]
        self.mu_, self.sd_ = X.mean(), X.std().replace(0, 1)
        Xs = sm.add_constant((X - self.mu_) / self.sd_)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if self.family == "nb":
                self.m = sm.NegativeBinomial(d.defensive_contribution, Xs,
                                             exposure=d.minutes / 90).fit(disp=0, maxiter=200)
                self.beta, self.alpha = self.m.params.values[:-1], self.m.params.values[-1]
            else:
                self.m = sm.GLM(d.defensive_contribution, Xs, family=sm.families.Poisson(),
                                exposure=d.minutes / 90).fit()
                self.beta, self.alpha = self.m.params.values, 0.0
        return self

    def rate(self, rows):
        Xs = sm.add_constant((rows[FEATURES] - self.mu_) / self.sd_, has_constant="add")
        return np.exp(Xs.values @ self.beta)

    def p_hit(self, rows, thr):
        r = self.rate(rows)
        p = np.zeros(len(rows))
        for col, mins in SCEN:
            mu = np.clip(r * mins / 90, 1e-6, None)
            if self.alpha > 0:
                n = 1 / self.alpha
                sf = nbinom.sf(thr - 1, n, n / (n + mu))
            else:
                sf = poisson.sf(thr - 1, mu)
            p += rows[col].values * sf
        return p


def metrics(d, col):
    y, p = d.hit.values, np.clip(d[col].values, 1e-6, 1 - 1e-6)
    starters = d[d.p_start >= 0.5]
    return {
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "brier": float(np.mean((p - y) ** 2)),
        "pred_hit_rate": float(p.mean()), "actual_hit_rate": float(y.mean()),
        "dc_xpts_mae": float(np.mean(np.abs(2 * p - 2 * y))),
        "dc_xpts_bias": float(np.mean(2 * p - 2 * y)),
        "rank_starters": float(starters.groupby("season_gw").apply(
            lambda g: g[col].rank().corr(g.hit.rank()), include_groups=False).mean()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "agent" / "glm" / "dc_v1.md"))
    a = ap.parse_args()
    df = v2.add_v2(v1.load())
    df = df[df.season.isin(["2025-26", "2026-27"]) & df.position_code.isin(POSITIONS)].copy()
    df["defensive_contribution"] = df.defensive_contribution.fillna(0)
    played = df.minutes > 0
    pos_rate = (df[played & (df.season == "2025-26")].groupby("position_code")
                .apply(lambda g: g.defensive_contribution.sum() / (g.minutes.sum() / 90), include_groups=False))
    df = prepare(df, pos_rate)
    df["thr"] = df.position_code.map(THRESH)
    df["hit"] = (df.defensive_contribution >= df.thr).astype(int)
    df["p_formula"] = df.xp_defensive_contribution / 2

    cur = df[df.season == "2026-27"]
    parts = []
    for gw in sorted(cur.gameweek.unique()):
        tr = df[((df.season == "2025-26") | (df.gameweek < gw) & (df.season == "2026-27")) & (df.minutes > 0)]
        te = cur[cur.gameweek == gw].copy()
        for fam in ("nb", "poisson"):
            te[f"p_{fam}"] = np.nan
            for pos in POSITIONS:
                m = Count(fam).fit(tr[tr.position_code == pos])
                mask = te.position_code == pos
                te.loc[mask, f"p_{fam}"] = m.p_hit(te[mask], THRESH[pos])
        parts.append(te)
        print(f"GW{gw}", flush=True)
    wf = pd.concat(parts)

    rows = []
    for pos in POSITIONS + ["DEF+MID"]:
        d = wf[wf.position_code.isin(["DEF", "MID"])] if pos == "DEF+MID" else wf[wf.position_code == pos]
        for col, name in (("p_formula", "formula (live)"), ("p_nb", "NB count model"), ("p_poisson", "Poisson count model")):
            rows.append({"position": pos, "model": name, **metrics(d, col)})
    res = pd.DataFrame(rows).set_index(["position", "model"])
    print(res.round(4).to_string())

    dm = wf[wf.position_code.isin(["DEF", "MID"])].copy()
    cal = []
    for col, name in (("p_formula", "formula"), ("p_nb", "NB")):
        dm["dec"] = pd.qcut(dm[col].rank(method="first"), 10, labels=False) + 1
        g = dm.groupby("dec").agg(pred=(col, "mean"), actual=("hit", "mean"), n=("hit", "size"))
        g.columns = [f"{name}_{c}" for c in g.columns]
        cal.append(g)
    cal = pd.concat(cal, axis=1)

    final = df[(df.minutes > 0)]
    coef_rows = []
    for pos in POSITIONS:
        m = Count("nb").fit(final[final.position_code == pos])
        names = ["const"] + FEATURES + ["alpha"]
        for k, b, se in zip(names, m.m.params.values, m.m.bse.values):
            coef_rows.append({"pos": pos, "term": k, "coef_per_sd": b, "se": se, "z": b / se})
    coefs = pd.DataFrame(coef_rows).set_index(["pos", "term"])

    text = ["# D118 v1: defensive contribution as a count model", "", __doc__.strip(), "",
            f"Position DC per 90 (2025/26, the shrinkage prior): "
            + ", ".join(f"{k} {v:.2f}" for k, v in pos_rate.items()), "",
            "## 2026/27 walk-forward GW1-%d (all rows, incl. non-starters)" % cur.gameweek.max(), "",
            "Lower log-loss / Brier / MAE is better; rank = within-GW Spearman of P(hit) vs the hit "
            "among likely starters (P(start) >= 0.5).", "",
            v1.md(res, floatfmt=".4f"), "",
            "## Calibration by decile of P(hit), DEF + MID", "",
            v1.md(cal, floatfmt=".3f"), "",
            "## NB coefficients (fitted on everything through the last played GW; per SD, log rate)", "",
            "alpha = NB2 dispersion (variance = mu + alpha mu^2).", "",
            v1.md(coefs, floatfmt=".3f"), ""]
    out = pathlib.Path(a.out)
    out.write_text("\n".join(text), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
