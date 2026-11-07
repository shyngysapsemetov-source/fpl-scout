"""Points -> overall rank curve for a past gameweek (D104), from public manager histories.

Usage:  python agent/rank_curve.py [--gw 9] [--samples 36]

Samples managers across the overall league's standings (league 314), reads each one's
public history, and keeps only (total points after GW, overall rank after GW). Pages are
spread log-evenly from the top to near the bottom. Nothing identifying is stored: no names,
team names or ids. One request at a time with a pause, as with the rest of the pipeline.
Writes data/agent/rank_curve_gw{N}.csv; `rank_for(points)` interpolates on log-rank.
"""
import argparse
import math
import pathlib
import time

import numpy as np
import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "fpl-scout (https://github.com/shyngysapsemetov-source/fpl-scout)"}
PAUSE = 1.0


def get(s, path):
    for attempt in range(4):
        r = s.get(f"{BASE}/{path}", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 ** attempt * 3)
    raise SystemExit(f"{path}: HTTP {r.status_code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, default=9)
    ap.add_argument("--samples", type=int, default=36)
    a = ap.parse_args()
    s = requests.Session()
    total_players = get(s, "bootstrap-static/")["total_players"]
    max_page = max(1, total_players // 50 - 10)
    pages = sorted({max(1, int(round(math.exp(x)))) for x in
                    np.linspace(0, math.log(max_page), a.samples)})
    rows = []
    for page in pages:
        time.sleep(PAUSE)
        st = get(s, f"leagues-classic/314/standings/?page_standings={page}")
        results = st["standings"]["results"]
        if not results:
            continue
        entry = results[len(results) // 2]["entry"]
        time.sleep(PAUSE)
        hist = get(s, f"entry/{entry}/history/")["current"]
        h = next((x for x in hist if x["event"] == a.gw), None)
        if h:
            rows.append({"total_points": h["total_points"], "overall_rank": h["overall_rank"]})
            print(f"page {page:>7}: GW{a.gw} total {h['total_points']:>4}  rank {h['overall_rank']:>9,}")
    df = pd.DataFrame(rows).sort_values("total_points", ascending=False)
    out = ROOT / "data" / "agent" / f"rank_curve_gw{a.gw}.csv"
    df.to_csv(out, index=False)
    print(f"wrote {out} ({len(df)} points; {total_players:,} managers)")


def rank_for(points, gw=9):
    df = pd.read_csv(ROOT / "data" / "agent" / f"rank_curve_gw{gw}.csv").sort_values("total_points")
    return float(np.exp(np.interp(points, df.total_points, np.log(df.overall_rank))))


if __name__ == "__main__":
    main()
