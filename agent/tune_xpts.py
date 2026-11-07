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


def build(cfg):
    subprocess.run([str(DBT), "run", "--profiles-dir", ".", "-q", "-s",
                    "int_player_prematch_features", "fct_player_expected_points",
                    "--vars", json.dumps(cfg)], cwd=ROOT / "dbt", check=True)


def score(season):
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select e.gameweek, e.position_code, e.xpts, e.actual_points,
               f.starts_prior / nullif(f.fixtures_prior, 0) >= 0.5 as likely_starter
        from main.fct_player_expected_points e
        join main.int_player_prematch_features f using (player_key, fixture_id)
        where e.season = ? and not e.is_upcoming""", [season]).df()
    con.close()
    out = {}
    for name, d in (("all", df), ("starters", df[df.likely_starter.fillna(False)])):
        out[f"{name}_mae"] = (d.xpts - d.actual_points).abs().mean()
        out[f"{name}_corr"] = d.xpts.corr(d.actual_points)
        out[f"{name}_rank"] = (d.groupby("gameweek")
                               .apply(lambda g: g.xpts.rank().corr(g.actual_points.rank()),
                                      include_groups=False).mean())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--out", default=str(ROOT / "data" / "agent" / "xpts_tuning.csv"))
    a = ap.parse_args()
    rows = []
    for values in itertools.product(*GRID.values()):
        cfg = dict(zip(GRID, values))
        build(cfg)
        rows.append({**cfg, **score(a.season)})
        print(json.dumps(rows[-1]), flush=True)
    pd.DataFrame(rows).to_csv(a.out, index=False)
    build({})   # leave the models at v0 defaults until a configuration is chosen
    print("wrote", a.out)


if __name__ == "__main__":
    main()
