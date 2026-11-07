"""Grid-search xPts parameters on the tuning season only (D86, D91).

Usage:  python agent/tune_xpts.py [--season 2024-25] [--out data/agent/xpts_tuning.csv]

For each configuration it rebuilds the two xPts models with dbt --vars, then scores the tuning
season. Likely starters are a FIXED set (raw pre-match start rate >= 0.5), so every
configuration is judged on the same player-fixtures. The hold-out season is never read here.
"""
import argparse
import itertools
import json
import os
import pathlib
import subprocess

import duckdb
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DBT = ROOT / ".venv" / "Scripts" / "dbt"
DB = ROOT / "data" / "dev.duckdb"
GRID = {"xpts_k_start": [1, 2, 3], "xpts_form_blend": [0.0, 0.3, 0.5], "xpts_prev_weight": [0, 3, 6]}
# D93: fading previous-season prior (the other parameters stay at their locked values).
GRID_FADE = {"xpts_prev_w0": [0, 6, 10, 15], "xpts_prev_fade": [6, 10, 15]}
# D96: shrinkage strength, tuned for top-pick accuracy (points captured by the top 11).
GRID_TOPPICK = {"xpts_k": [1, 2, 3], "xpts_k_min": [90, 180, 270]}


def build(cfg):
    subprocess.run([str(DBT), "run", "--profiles-dir", ".", "-q", "-s",
                    "int_player_prematch_features", "fct_player_expected_points",
                    "--vars", json.dumps(cfg)], cwd=ROOT / "dbt", check=True)


def score(season):
    """Metrics on the tuning season, split early (GW1-9) and late (GW10+) (D93) as well as
    overall, so a full-season average can't hide an early-season weakness."""
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select e.gameweek, e.position_code, e.xpts, e.actual_points,
               f.starts_prior / nullif(f.fixtures_prior, 0) >= 0.5 as likely_starter
        from main.fct_player_expected_points e
        join main.int_player_prematch_features f using (player_key, fixture_id)
        where e.season = ? and not e.is_upcoming""", [season]).df()
    con.close()
    out = {}
    # Points captured: actual points of the top 11 by xPts / of the best possible 11, per GW.
    cap = df.groupby("gameweek").apply(
        lambda g: g.nlargest(11, "xpts").actual_points.sum() / g.nlargest(11, "actual_points").actual_points.sum(),
        include_groups=False)
    top20 = df.groupby("gameweek").apply(lambda g: g.nlargest(20, "xpts").actual_points.mean(),
                                         include_groups=False)
    out["captured_top11"] = cap.mean()
    out["captured_top11_early"] = cap[cap.index <= 9].mean()
    out["top20_avg_points"] = top20.mean()
    for name, d in (("all", df), ("starters", df[df.likely_starter.fillna(False)]),
                    ("early", df[df.gameweek <= 9]), ("late", df[df.gameweek >= 10])):
        out[f"{name}_mae"] = (d.xpts - d.actual_points).abs().mean()
        out[f"{name}_bias"] = (d.xpts - d.actual_points).mean()
        out[f"{name}_corr"] = d.xpts.corr(d.actual_points)
        out[f"{name}_rank"] = (d.groupby("gameweek")
                               .apply(lambda g: g.xpts.rank().corr(g.actual_points.rank()),
                                      include_groups=False).mean())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--out", default=str(ROOT / "data" / "agent" / "xpts_tuning_toppick.csv"))
    ap.add_argument("--grid", choices=["base", "fade", "toppick"], default="toppick")
    a = ap.parse_args()
    rows = []
    grid = {"fade": GRID_FADE, "toppick": GRID_TOPPICK, "base": GRID}[a.grid]
    for values in itertools.product(*grid.values()):
        cfg = dict(zip(grid, values))
        build(cfg)
        rows.append({**cfg, **score(a.season)})
        print(json.dumps(rows[-1]), flush=True)
    pd.DataFrame(rows).to_csv(a.out, index=False)
    build({})   # leave the models at v0 defaults until a configuration is chosen
    print("wrote", a.out)


if __name__ == "__main__":
    main()
