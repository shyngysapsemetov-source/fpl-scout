"""Fit per-position component scales for xPts (D108) and write them to a dbt seed.

Usage:  python agent/fit_components.py [--season 2024-25]

On the tuning season, for each outfield position:
  goals / assists / defensive contribution: scale = actual points from that category / the
    model's expected points from it (a level fix, e.g. goals are over-predicted for FWD);
  bonus: least squares of actual bonus on clean-sheet probability and expected attacking points
    (xp_goals + xp_assists), replacing the per-90 bonus rate (near noise, per the audit).
Keepers keep their own rules (D100). Writes dbt/seeds/component_scales.csv.
"""
import argparse
import os
import pathlib

import duckdb
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
SEED = ROOT / "dbt" / "seeds" / "component_scales.csv"
GOAL_PTS = {"DEF": 6, "MID": 5, "FWD": 4}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--dc-season", default="2025-26",
                    help="defensive contribution only exists from 2025/26; fitted there")
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select e.season, e.position_code, e.xp_goals, e.xp_assists, e.xp_defensive_contribution,
               e.xp_bonus, e.clean_sheet_prob_used,
               e.xp_goals / nullif(e.p_60 + e.p_early + e.p_sub, 0) as xp_goals_per_play,
               e.xp_assists / nullif(e.p_60 + e.p_early + e.p_sub, 0) as xp_assists_per_play,
               a.goals_scored, a.assists, a.bonus,
               a.defensive_contribution, a.minutes
        from main.fct_player_expected_points e
        join main.int_player_fixtures_all a using (player_key, fixture_id)
        where not e.is_upcoming and e.position_code <> 'GKP'
          and e.season in (?, ?)""", [a.season, a.dc_season]).df()
    lines = ["position_code,component,a,b,c,fitted_on"]
    for pos in ("DEF", "MID", "FWD"):
        d = df[(df.position_code == pos) & (df.season == a.season)]
        g = (d.goals_scored * GOAL_PTS[pos]).sum() / d.xp_goals.sum()
        s = (d.assists * 3).sum() / d.xp_assists.sum()
        dd = df[(df.position_code == pos) & (df.season == a.dc_season)]
        thr = 10 if pos == "DEF" else 12
        dc = ((dd.defensive_contribution >= thr) * 2).sum() / dd.xp_defensive_contribution.sum()
        played = d[d.minutes > 0]   # bonus per match played; the model multiplies by P(plays)
        X = np.column_stack([np.ones(len(played)), played.clean_sheet_prob_used,
                             played.xp_goals_per_play + played.xp_assists_per_play])
        coef, *_ = np.linalg.lstsq(X, played.bonus.astype(float), rcond=None)
        lines += [f"{pos},goals,{g:.4f},0,0,{a.season}", f"{pos},assists,{s:.4f},0,0,{a.season}",
                  f"{pos},defensive_contribution,{dc:.4f},0,0,{a.dc_season}",
                  f"{pos},bonus,{coef[0]:.4f},{coef[1]:.4f},{coef[2]:.4f},{a.season}"]
        print(f"{pos}: goals x{g:.2f}  assists x{s:.2f}  DC x{dc:.2f}  "
              f"bonus = {coef[0]:+.3f} {coef[1]:+.3f}*CS {coef[2]:+.3f}*xGIpts (per fixture, x P(plays))")
    SEED.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", SEED)


if __name__ == "__main__":
    main()
