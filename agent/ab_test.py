"""A/B a model change on the hold-out seasons (D94: judged as a ranking tool).

Usage:  python agent/ab_test.py '{"xpts_minutes_model": false}' '{"xpts_minutes_model": true}'

Rebuilds the xPts models with each --vars setting in turn and scores 2025/26 (hold-out) and
2026/27 GW1-9 (unseen) with tune_xpts.score: within-GW rank (all players and fixed likely
starters), top-11 points captured, top-20 average points, MAE. Leaves the models built with
the LAST setting.
"""
import json
import sys

import tune_xpts as t

KEYS = ["captured_top11", "top20_avg_points", "starters_rank", "all_rank", "early_rank", "all_mae"]


def num(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def main():
    for raw in sys.argv[1:]:
        cfg = json.loads(raw)
        t.build(cfg)
        for season in ("2025-26", "2026-27"):
            r = t.score(season)
            print(f"{raw:<45} {season}  " + "  ".join(f"{k}={num(r[k])}" for k in KEYS), flush=True)


if __name__ == "__main__":
    main()
