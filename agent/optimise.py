"""Squad and transfer optimiser on expected points (D81, D97-D99).

Usage (also available as `scout.py optimise ...`):
  python agent/optimise.py squad [--budget 102.0] [--horizon 5] [--fix "Name" ...] [--exclude "Name" ...]
  python agent/optimise.py transfers --team team.json [--bank 1.2] [--free 1] [--max-transfers 3]

Objective: the weighted sum over the next `horizon` gameweeks (weights 1.0, 0.9, 0.8, 0.7,
0.6, D97) of the starting XI's calibrated xPts, plus the captain's xPts again, plus 10% of
the bench's xPts (D99). The XI and captain are chosen per gameweek; the 15 are held for the
whole horizon (later transfers aren't modelled). Squad rules: 2 GKP, 5 DEF, 5 MID, 3 FWD, max 3
per club, budget; each XI has 1 GK, 3+ DEF, 1+ FWD.

Transfers mode keeps the current squad unless a change pays: each transfer beyond the free ones
costs 4 points (D98), and a sale uses the selling price in team.json ("selling_prices":
{"Name (TEAM)": 6.1, ...}) or, if absent, the current price. It reports when rolling the
transfer is best.
"""
import argparse
import json
import os
import pathlib
import sys

import duckdb
import pandas as pd
import pulp

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))
WEIGHTS = [1.0, 0.9, 0.8, 0.7, 0.6]
BENCH = 0.1
QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def load(horizon):
    con = duckdb.connect(str(DB), read_only=True)
    players = con.execute("""select player_id, web_name, current_team as team, position_code as pos,
                                    price, status, chance_of_playing_next_pct as chance
                             from main.mart_player_current""").df()
    xp = con.execute("""
        select player_id, gameweek, sum(xpts_calibrated) as xp
        from main.fct_player_expected_points
        where is_upcoming group by all""").df()
    gws = sorted(xp.gameweek.unique())[:horizon]
    xp = xp[xp.gameweek.isin(gws)].pivot(index="player_id", columns="gameweek", values="xp").fillna(0)
    players = players.set_index("player_id").join(xp, how="inner").reset_index()
    players["label"] = players.web_name + " (" + players.team + ")"
    return players, gws


def find(players, name):
    hit = players[players.label == name]
    if hit.empty:
        hit = players[players.web_name == name]
    if len(hit) != 1:
        sys.exit(f"cannot resolve {name!r}; use 'Name (TEAM)'")
    return int(hit.index[0])


def solve(players, gws, budget, current=None, sell=None, bank=0.0, free=1, max_transfers=None,
          fix=(), exclude=()):
    idx = list(players.index)
    w = dict(zip(gws, WEIGHTS[:len(gws)]))
    prob = pulp.LpProblem("fpl", pulp.LpMaximize)
    pick = {i: pulp.LpVariable(f"p{i}", cat="Binary") for i in idx}
    start = {(i, g): pulp.LpVariable(f"s{i}_{g}", cat="Binary") for i in idx for g in gws}
    cap = {(i, g): pulp.LpVariable(f"c{i}_{g}", cat="Binary") for i in idx for g in gws}
    xp = lambda i, g: float(players.at[i, g])
    obj = pulp.lpSum(w[g] * xp(i, g) * (start[i, g] + cap[i, g] + BENCH * (pick[i] - start[i, g]))
                     for i in idx for g in gws)
    hits = None
    if current is not None:
        n_out = pulp.lpSum(1 - pick[i] for i in current)
        hits = pulp.LpVariable("hits", lowBound=0, cat="Integer")
        prob += hits >= n_out - free
        obj -= 4 * hits
        if max_transfers is not None:
            prob += n_out <= max_transfers
        # Money: bank + selling prices of current players must cover the new squad.
        value = {i: (sell or {}).get(i, players.at[i, "price"]) for i in idx}
        prob += pulp.lpSum((value[i] if i in current else players.at[i, "price"]) * pick[i]
                           for i in idx) <= bank + sum(value[i] for i in current)
    else:
        prob += pulp.lpSum(players.at[i, "price"] * pick[i] for i in idx) <= budget
    prob += obj
    for p, n in QUOTA.items():
        prob += pulp.lpSum(pick[i] for i in idx if players.at[i, "pos"] == p) == n
    for t in players.team.unique():
        prob += pulp.lpSum(pick[i] for i in idx if players.at[i, "team"] == t) <= 3
    for i in fix:
        prob += pick[i] == 1
    for i in exclude:
        prob += pick[i] == 0
    for g in gws:
        prob += pulp.lpSum(start[i, g] for i in idx) == 11
        prob += pulp.lpSum(cap[i, g] for i in idx) == 1
        for pos, lo, hi in (("GKP", 1, 1), ("DEF", 3, 5), ("MID", 2, 5), ("FWD", 1, 3)):
            n = pulp.lpSum(start[i, g] for i in idx if players.at[i, "pos"] == pos)
            prob += n >= lo
            prob += n <= hi
        for i in idx:
            prob += start[i, g] <= pick[i]
            prob += cap[i, g] <= start[i, g]
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=120))
    squad = [i for i in idx if pick[i].value() > 0.5]
    plan = {g: {"xi": [i for i in squad if start[i, g].value() > 0.5],
                "captain": next(i for i in squad if cap[i, g].value() > 0.5)} for g in gws}
    return squad, plan, (hits.value() if hits is not None else 0), pulp.value(prob.objective)


def report(players, gws, squad, plan, hits, objective, current=None):
    g0 = gws[0]
    xi = plan[g0]["xi"]
    bench = [i for i in squad if i not in xi]
    bench = sorted([i for i in bench if players.at[i, "pos"] == "GKP"]) + \
        sorted([i for i in bench if players.at[i, "pos"] != "GKP"], key=lambda i: -players.at[i, g0])
    lab = lambda i: players.at[i, "label"]
    cost = players.loc[squad, "price"].sum()
    form = "-".join(str(sum(players.at[i, "pos"] == p for i in xi)) for p in ("DEF", "MID", "FWD"))
    print(f"Optimised over GW{gws[0]}-GW{gws[-1]} (weights {WEIGHTS[:len(gws)]}, bench 10%, calibrated xPts)")
    print(f"Weighted objective {objective:.1f} · squad cost £{cost:.1f}m · transfers hits: {int(hits or 0)}")
    if current is not None:
        out = [i for i in current if i not in squad]
        inn = [i for i in squad if i not in current]
        # Pair each sale with a purchase in the same position (quotas guarantee a match).
        pairs = []
        for o in out:
            n = next(n for n in inn if players.at[n, "pos"] == players.at[o, "pos"])
            inn.remove(n)
            pairs.append((o, n))
        print("Transfers: " + (", ".join(f"{lab(o)} -> {lab(n)} ({players.at[o, 'pos']})" for o, n in pairs)
                               or "none: roll the transfer"))
    print(f"\nGW{g0} XI ({form}), captain {lab(plan[g0]['captain'])}:")
    for p in ("GKP", "DEF", "MID", "FWD"):
        for i in sorted([i for i in xi if players.at[i, "pos"] == p], key=lambda i: -players.at[i, g0]):
            print(f"  {p} {lab(i):<26} £{players.at[i, 'price']:>4.1f}  xP GW{g0} {players.at[i, g0]:.2f}  "
                  f"next {len(gws)}: {sum(players.at[i, g] for g in gws):.1f}")
    print("Bench (in order): " + ", ".join(f"{lab(i)} {players.at[i, g0]:.1f}" for i in bench))
    print("Captain by GW: " + ", ".join(f"GW{g} {players.at[plan[g]['captain'], 'web_name']}" for g in gws))
    team = {"squad": [lab(i) for i in squad], "starting": [lab(i) for i in xi],
            "bench": [lab(i) for i in bench], "captain": lab(plan[g0]["captain"]),
            "vice_captain": lab(sorted(xi, key=lambda i: -players.at[i, g0])[1]
                                if sorted(xi, key=lambda i: -players.at[i, g0])[0] == plan[g0]["captain"]
                                else sorted(xi, key=lambda i: -players.at[i, g0])[0])}
    print("\nteam.json:", json.dumps(team, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("squad")
    s.add_argument("--budget", type=float, default=102.0)
    t = sub.add_parser("transfers")
    t.add_argument("--team", required=True)
    t.add_argument("--bank", type=float, default=0.0)
    t.add_argument("--free", type=int, default=1)
    t.add_argument("--max-transfers", type=int)
    for p in (s, t):
        p.add_argument("--horizon", type=int, default=5)
        p.add_argument("--fix", nargs="*", default=[], help="players that must be in the squad")
        p.add_argument("--exclude", nargs="*", default=[], help="players to leave out")
    a = ap.parse_args()
    players, gws = load(a.horizon)
    fix = [find(players, n) for n in a.fix]
    exclude = [find(players, n) for n in a.exclude]
    if a.mode == "squad":
        squad, plan, hits, obj = solve(players, gws, a.budget, fix=fix, exclude=exclude)
        report(players, gws, squad, plan, hits, obj)
    else:
        team = json.loads(pathlib.Path(a.team).read_text(encoding="utf-8"))
        current = [find(players, n) for n in team["squad"]]
        sell = {find(players, n): v for n, v in team.get("selling_prices", {}).items()}
        squad, plan, hits, obj = solve(players, gws, None, current=current, sell=sell, bank=a.bank,
                                       free=a.free, max_transfers=a.max_transfers, fix=fix, exclude=exclude)
        report(players, gws, squad, plan, hits, obj, current=current)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
