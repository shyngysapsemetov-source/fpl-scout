"""Backtest expected points against what happened (D86, D90).

Usage:  python agent/backtest.py [--seasons 2024-25 2025-26]

Every forecast in fct_player_expected_points is made before kickoff, so each past fixture is
an honest out-of-sample test. Compared:
  xpts        our structural model (D42)
  naive       stabilised season points per match x P(plays)
  fpl_shift   FPL's archived xP from the player's previous gameweek row: the fair FPL bar
  fpl_raw     FPL's archived xP as stored; likely post-match (D90), so an upper bound only

Metrics per season and position, on every player-fixture and on likely starters (pre-match
P(start) >= 0.5, the players a manager actually chooses between):
  MAE         mean absolute error in points
  corr        Pearson correlation with actual points
  rank        mean Spearman rank correlation within each gameweek: does it order players right?
"""
import argparse
import os
import pathlib

import duckdb
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
MODELS = ["xpts", "naive", "fpl_shift", "fpl_raw"]


def load(seasons):
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select season, player_key, position_code, gameweek, kickoff_at, p_start,
               xpts, naive_xpts as naive, fpl_xp as fpl_raw, actual_points
        from main.fct_player_expected_points
        where not is_upcoming and season in (select unnest(?))""", [seasons]).df()
    df = df.sort_values(["player_key", "kickoff_at"])
    df["fpl_shift"] = df.groupby("player_key").fpl_raw.shift(1)
    return df


def metrics(d):
    out = {}
    for m in MODELS:
        x = d.dropna(subset=[m])
        if x.empty:
            continue
        rank = (x.groupby("gameweek")
                # Spearman = Pearson on ranks (no scipy dependency).
                .apply(lambda g: g[m].rank().corr(g.actual_points.rank()), include_groups=False)
                .mean())
        out[m] = {"n": len(x), "MAE": (x[m] - x.actual_points).abs().mean(),
                  "corr": x[m].corr(x.actual_points), "rank": rank}
    return out


def show(title, d):
    print(f"\n{title}  ({len(d)} player-fixtures)")
    print(f"  {'model':<10}{'MAE':>7}{'corr':>7}{'rank':>7}")
    for m, v in metrics(d).items():
        print(f"  {m:<10}{v['MAE']:>7.3f}{v['corr']:>7.3f}{v['rank']:>7.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", default=["2024-25", "2025-26"])
    a = ap.parse_args()
    df = load(a.seasons)
    for season in a.seasons:
        s = df[df.season == season]
        tag = "TUNE" if season == a.seasons[0] else "HOLD-OUT (report only)"
        print(f"\n===== {season} · {tag} =====")
        show("All player-fixtures", s)
        show("Likely starters (P(start) >= 0.5)", s[s.p_start >= 0.5])
        for pos in ["GKP", "DEF", "MID", "FWD"]:
            show(f"  {pos}, likely starters", s[(s.position_code == pos) & (s.p_start >= 0.5)])


if __name__ == "__main__":
    main()
