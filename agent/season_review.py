"""Walk-forward review of xPts on the current season (D92 follow-up).

Usage:  python agent/season_review.py [--season 2026-27]

Every xPts row is a pre-match forecast: GW1 uses only priors (last season's team ratings,
position averages), GW2 adds GW1, and so on. This compares each forecast with the points
actually scored, by gameweek, by position, by player and by decision (captain, best XI).
"""
import argparse
import os
import pathlib

import duckdb
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
pd.set_option("display.width", 160)


def rank_corr(g, a, b):
    return g[a].rank().corr(g[b].rank())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--col", default="xpts_calibrated", help="forecast column to review")
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        select e.player_id, e.player_name, e.position_code, e.gameweek, e.{col} as xpts, e.naive_xpts,
               e.actual_points, e.actual_minutes, e.p_start, f.starts
        from main.fct_player_expected_points e
        join main.int_player_fixtures_all f using (player_key, fixture_id)
        where e.season = ? and not e.is_upcoming""".format(col=a.col), [a.season]).df()
    # Double gameweeks: one forecast and one score per player per GW.
    gw = (df.groupby(["player_id", "player_name", "position_code", "gameweek"], as_index=False)
            .agg(xpts=("xpts", "sum"), naive=("naive_xpts", "sum"), actual=("actual_points", "sum"),
                 minutes=("actual_minutes", "sum"), p_start=("p_start", "mean")))
    gw["err"] = gw.xpts - gw.actual
    starters = gw[gw.p_start >= 0.5]

    print(f"forecast column: {a.col}")
    print(f"WALK-FORWARD REVIEW {a.season}: {gw.gameweek.nunique()} gameweeks, "
          f"{gw.player_id.nunique()} players, {len(gw)} player-gameweeks "
          f"({len(starters)} with pre-match P(start) >= 0.5)\n")

    print("1. By gameweek (likely starters): bias = mean(xPts - actual); rank = within-GW "
          "Spearman; top-20 hit = share of xPts top 20 who were in the actual top 20")
    rows = []
    for g, d in starters.groupby("gameweek"):
        top_x = set(d.nlargest(20, "xpts").player_id)
        top_a = set(d.nlargest(20, "actual").player_id)
        # Points captured: actual points of the forecast's top 11 / of the best possible 11.
        captured = d.nlargest(11, "xpts").actual.sum() / d.nlargest(11, "actual").actual.sum()
        captured_naive = d.nlargest(11, "naive").actual.sum() / d.nlargest(11, "actual").actual.sum()
        rows.append({"GW": g, "n": len(d), "pred_mean": d.xpts.mean(), "actual_mean": d.actual.mean(),
                     "bias": d.err.mean(), "MAE": d.err.abs().mean(),
                     "corr": d.xpts.corr(d.actual), "rank": rank_corr(d, "xpts", "actual"),
                     "naive_rank": rank_corr(d, "naive", "actual"),
                     "top20_hit": len(top_x & top_a) / 20, "captured": captured,
                     "captured_naive": captured_naive})
    t = pd.DataFrame(rows)
    print(t.round(2).to_string(index=False))
    print(f"   season: MAE {starters.err.abs().mean():.2f}, bias {starters.err.mean():+.2f}, "
          f"corr {starters.xpts.corr(starters.actual):.3f}, mean within-GW rank {t['rank'].mean():.3f} "
          f"(naive {t.naive_rank.mean():.3f}), top-20 hit {t.top20_hit.mean():.0%}\n")

    print("2. By position (likely starters, all GWs)")
    pos = starters.groupby("position_code").apply(lambda d: pd.Series({
        "n": len(d), "pred_mean": d.xpts.mean(), "actual_mean": d.actual.mean(),
        "bias": d.err.mean(), "MAE": d.err.abs().mean(), "corr": d.xpts.corr(d.actual)}),
        include_groups=False)
    print(pos.round(2).to_string(), "\n")

    print("3. Calibration (all player-gameweeks, by xPts decile)")
    gw["decile"] = pd.qcut(gw.xpts.rank(method="first"), 10, labels=False) + 1
    cal = gw.groupby("decile").agg(n=("xpts", "size"), predicted=("xpts", "mean"), actual=("actual", "mean"))
    print(cal.round(2).to_string(), "\n")

    print("4. Players: season total forecast vs actual (GW1-9 sums)")
    pl = gw.groupby(["player_name", "position_code"], as_index=False).agg(
        xpts=("xpts", "sum"), actual=("actual", "sum"), gws=("gameweek", "nunique"))
    pl["diff"] = pl.actual - pl.xpts
    print("   Most UNDER-predicted (scored more than forecast):")
    print(pl.nlargest(10, "diff").round(1).to_string(index=False))
    print("   Most OVER-predicted (scored less than forecast):")
    print(pl.nsmallest(10, "diff").round(1).to_string(index=False))
    big = pl[pl.actual >= 30]
    print(f"   Players with 30+ actual points: {len(big)}; correlation of season totals "
          f"{pl.xpts.corr(pl.actual):.3f}; mean |diff| for them {big['diff'].abs().mean():.1f} pts\n")

    print("5. Decisions")
    cap = []
    for g, d in gw.groupby("gameweek"):
        pick = d.loc[d.xpts.idxmax()]
        best = d.loc[d.actual.idxmax()]
        rank = int((d.actual > pick.actual).sum()) + 1
        cap.append({"GW": g, "xPts captain": pick.player_name, "forecast": pick.xpts,
                    "scored": pick.actual, "rank of his score": rank,
                    "actual best": best.player_name, "best scored": best.actual})
    print(pd.DataFrame(cap).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
