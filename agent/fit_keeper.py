"""Fit the keeper-specific xPts parameters (D100) and write them to a dbt seed.

Usage:  python agent/fit_keeper.py [--seasons 2023-24 2024-25]

Fitted on the given (training) seasons only, from pre-match inputs:
  p_start_if_started_last / p_start_if_not   P(start) by whether he started his last fixture
  bonus_intercept, bonus_cs_slope            bonus per match played ~ a + b * clean-sheet prob
  saves_intercept, saves_xga_slope           save points (floor(saves/3)) per match played
                                             ~ a + b * expected goals against
Writes dbt/seeds/keeper_params.csv; the xPts model reads it for goalkeepers.
"""
import argparse
import os
import pathlib

import duckdb
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
SEED = ROOT / "dbt" / "seeds" / "keeper_params.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", default=["2023-24", "2024-25"])
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select f.season, f.started_last, a.starts, a.minutes, a.bonus, a.saves,
               x.clean_sheet_prob, x.expected_goals_against
        from main.int_player_prematch_features f
        join main.int_player_fixtures_all a using (player_key, fixture_id)
        join main.fct_team_fixture_expectations x
          on x.season = f.season and x.fixture_id = f.fixture_id and x.team_code = f.team_code
        where f.position_code = 'GKP' and not f.is_upcoming and f.season in (select unnest(?))
          and f.started_last is not null""", [a.seasons]).df()
    p1 = df[df.started_last == 1].starts.mean()
    p0 = df[df.started_last == 0].starts.mean()
    played = df[df.minutes > 0]
    bs, bi = np.polyfit(played.clean_sheet_prob, played.bonus, 1)
    ss, si = np.polyfit(played.expected_goals_against, played.saves // 3, 1)
    rows = {"p_start_if_started_last": p1, "p_start_if_not": p0, "bonus_intercept": bi,
            "bonus_cs_slope": bs, "saves_intercept": si, "saves_xga_slope": ss}
    for k, v in rows.items():
        print(f"{k:<26} {v:.4f}")
    SEED.write_text("parameter,value,fitted_on\n" + "".join(
        f"{k},{v:.6f},{'+'.join(a.seasons)}\n" for k, v in rows.items()), encoding="utf-8")
    print("wrote", SEED, f"(n={len(df)} keeper fixtures, {len(played)} played)")


if __name__ == "__main__":
    main()
