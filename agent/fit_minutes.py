"""Fit the outfield start-probability model (D106) and write it to a dbt seed.

Usage:  python agent/fit_minutes.py [--seasons 2023-24 2024-25]

P(start) = clip(a + b1 * start_rate + b2 * started_last + b3 * last_minutes / 90
                + b4 * min(start_streak, 5) / 5, 0.01, 0.99)
a linear-probability model fitted by least squares on outfield players' fixtures in the given
(training) seasons, where he has a previous fixture this season. start_rate is the existing
stabilised rate; the new terms add what happened in his last fixture and his current run of
starts. Writes dbt/seeds/minutes_params.csv; the xPts model reads it for DEF/MID/FWD.
"""
import argparse
import os
import pathlib

import duckdb
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
SEED = ROOT / "dbt" / "seeds" / "minutes_params.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", default=["2023-24", "2024-25"])
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select f.start_rate, f.started_last, f.last_minutes, f.start_streak, a.starts
        from main.int_player_prematch_features f
        join main.int_player_fixtures_all a using (player_key, fixture_id)
        where f.position_code <> 'GKP' and not f.is_upcoming and f.started_last is not null
          and f.season in (select unnest(?))""", [a.seasons]).df()
    X = np.column_stack([np.ones(len(df)), df.start_rate, df.started_last, df.last_minutes / 90,
                         np.minimum(df.start_streak, 5) / 5])
    coef, *_ = np.linalg.lstsq(X, df.starts.astype(float), rcond=None)
    names = ["intercept", "b_start_rate", "b_started_last", "b_last_minutes", "b_streak"]
    pred = np.clip(X @ coef, 0.01, 0.99)
    brier_new = ((pred - df.starts) ** 2).mean()
    brier_old = ((df.start_rate - df.starts) ** 2).mean()
    for n, c in zip(names, coef):
        print(f"{n:<16} {c:+.4f}")
    print(f"in-sample Brier: start_rate alone {brier_old:.4f} -> model {brier_new:.4f} (n={len(df)})")
    SEED.write_text("parameter,value,fitted_on\n" + "".join(
        f"{n},{c:.6f},{'+'.join(a.seasons)}\n" for n, c in zip(names, coef)), encoding="utf-8")
    print("wrote", SEED)


if __name__ == "__main__":
    main()
