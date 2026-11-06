"""Pre-match baseline squads for an agent experiment (D75), from the same frozen snapshot.

Usage:  python agent/baselines.py [--budget 102.0]

Each baseline maximises one score with an integer program under the squad rules (15 players:
2 GKP, 5 DEF, 5 MID, 3 FWD; max 3 per club; budget), then picks the best valid XI by the same
score, captain = top scorer, vice = second, bench = reserve GK then outfield by score.

  ep_next  FPL's own expected points for the next gameweek (the API's `ep_next`)
  form     naive: season points per match, rank-eligible players only

Writes baseline_<name>.json next to the snapshot, in the agent's team format.
"""
import argparse
import json
import os
import pathlib

import duckdb
import pulp

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "agent" / "gw10" / "dev.duckdb"))
QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def players(con):
    return con.execute("""
        with ep as (
            select (e->>'id')::int as player_id, (e->>'ep_next')::double as ep_next
            from (select unnest(cast(payload->'elements' as json[])) e
                  from raw.bootstrap_static
                  where dt = (select max(dt) from raw.bootstrap_static)))
        select m.player_id, m.web_name, m.current_team, m.position_code, m.price, m.status,
               m.is_rank_eligible, m.points_per_match_season, ep.ep_next
        from main.mart_player_current m join ep using (player_id)""").df()


def best_squad(df, score, budget):
    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    pick = {i: pulp.LpVariable(f"p{i}", cat="Binary") for i in df.index}
    start = {i: pulp.LpVariable(f"s{i}", cat="Binary") for i in df.index}
    # Starters count fully, bench a little, so the squad is built around a strong XI.
    prob += pulp.lpSum(df.at[i, score] * (start[i] + 0.1 * pick[i]) for i in df.index)
    prob += pulp.lpSum(df.at[i, "price"] * pick[i] for i in df.index) <= budget
    for p, n in QUOTA.items():
        prob += pulp.lpSum(pick[i] for i in df.index if df.at[i, "position_code"] == p) == n
    for t in df.current_team.unique():
        prob += pulp.lpSum(pick[i] for i in df.index if df.at[i, "current_team"] == t) <= 3
    for i in df.index:
        prob += start[i] <= pick[i]
    prob += pulp.lpSum(start.values()) == 11

    def n_start(p):
        return pulp.lpSum(start[i] for i in df.index if df.at[i, "position_code"] == p)

    prob += n_start("GKP") == 1
    prob += n_start("DEF") >= 3
    prob += n_start("FWD") >= 1
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    squad = [i for i in df.index if pick[i].value() > 0.5]
    xi = [i for i in squad if start[i].value() > 0.5]
    return squad, xi


def label(df, i):
    return f"{df.at[i, 'web_name']} ({df.at[i, 'current_team']})"


def to_team(df, squad, xi, score, note):
    xi_sorted = sorted(xi, key=lambda i: -df.at[i, score])
    rest = [i for i in squad if i not in xi]
    gk = [i for i in rest if df.at[i, "position_code"] == "GKP"]
    out = sorted([i for i in rest if df.at[i, "position_code"] != "GKP"], key=lambda i: -df.at[i, score])
    return {"squad": [label(df, i) for i in squad], "starting": [label(df, i) for i in xi],
            "bench": [label(df, i) for i in gk + out],
            "captain": label(df, xi_sorted[0]), "vice_captain": label(df, xi_sorted[1]),
            "expected_points_note": note,
            "cost": round(float(df.loc[squad, "price"].sum()), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=102.0)
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    df = players(con).reset_index(drop=True)

    ep = df.fillna({"ep_next": 0})
    squad, xi = best_squad(ep, "ep_next", a.budget)
    total = ep.loc[xi, "ep_next"].sum() + ep.loc[xi, "ep_next"].max()
    team = to_team(ep, squad, xi, "ep_next", f"FPL ep_next for the XI incl. captain: {total:.1f}")
    (DB.parent / "baseline_ep_next.json").write_text(json.dumps(team, indent=2, ensure_ascii=False), encoding="utf-8")
    print("ep_next:", team["cost"], team["captain"], team["expected_points_note"])

    form = df[df.is_rank_eligible & (df.status == "a")].copy().fillna({"points_per_match_season": 0})
    squad, xi = best_squad(form, "points_per_match_season", a.budget)
    team = to_team(form, squad, xi, "points_per_match_season", "naive: season ppm, available players")
    (DB.parent / "baseline_form.json").write_text(json.dumps(team, indent=2, ensure_ascii=False), encoding="utf-8")
    print("form:", team["cost"], team["captain"])


if __name__ == "__main__":
    main()
