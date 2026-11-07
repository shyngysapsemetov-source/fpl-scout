"""Fit the xPts calibration stretch on the tuning season (D93) and write it to a dbt seed.

Usage:  python agent/fit_calibration.py [--season 2024-25]

xPts ranks players reasonably but compresses the range early in the season. For each phase
(GW1-9 'early', GW10+ 'late') and position this fits actual = a + b * xPts by least squares on the tuning
season only and writes dbt/seeds/xpts_calibration.csv. The model then publishes
xpts_calibrated = max(0, a + b * xpts) beside the raw xpts. A linear stretch leaves the ranking
within a gameweek unchanged; it fixes the level and the spread.
"""
import argparse
import os
import pathlib

import duckdb
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
SEED = ROOT / "dbt" / "seeds" / "xpts_calibration.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""select gameweek, position_code, xpts, actual_points
                        from main.fct_player_expected_points
                        where season = ? and not is_upcoming""", [a.season]).df()
    # Per phase AND position (D94): a position-dependent bias would tilt cross-position choices.
    lines = ["phase,position_code,gw_from,gw_to,intercept,slope,fitted_on,n"]
    for phase, lo, hi in (("early", 1, 9), ("late", 10, 99)):
        for pos in ("GKP", "DEF", "MID", "FWD"):
            d = df[(df.gameweek >= lo) & (df.gameweek <= hi) & (df.position_code == pos)]
            slope, intercept = np.polyfit(d.xpts, d.actual_points, 1)
            lines.append(f"{phase},{pos},{lo},{hi},{intercept:.4f},{slope:.4f},{a.season},{len(d)}")
            print(f"{phase:<5} {pos} GW{lo}-{hi}: actual = {intercept:+.3f} + {slope:.3f} x xPts  (n={len(d)})")
    SEED.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", SEED)


if __name__ == "__main__":
    main()
