"""Score experiment teams for a played gameweek (D73, D78-D80).

Usage:  python agent/score.py --gw 10 team1.json [team2.json ...] [--hits 0 4 ...]

Standard FPL scoring of a picked team: the starting XI's points, with automatic substitutions
for starters who played 0 minutes (bench order, keeping a valid formation: 1 GK, 3+ DEF, 1+
FWD; the reserve GK only replaces the GK), the captain's points doubled, or the vice's if
the captain played 0 minutes. Double gameweeks sum a player's fixtures. --hits subtracts
transfer hits per team, in the same order as the files.

Reads actual points from data/dev.duckdb (or FPL_SCOUT_DB): run it after the gameweek is
data_checked and the pipeline has been rebuilt.
"""
import argparse
import json
import os
import pathlib

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "dev.duckdb"))


def resolve(name, players):
    """'Name (TEAM)' or a unique 'Name' -> player_id."""
    if name.endswith(")") and " (" in name:
        n, t = name[:-1].rsplit(" (", 1)
        hits = [p for p in players if p["web_name"] == n and p["team"] == t.split(",")[0]]
    elif name.endswith("..."):
        # Truncated in the app, e.g. "Calvert-Le...": match on the visible prefix.
        hits = [p for p in players if p["web_name"].startswith(name[:-3])]
    else:
        hits = [p for p in players if p["web_name"] == name]
    if len(hits) > 1 and "(" not in name:
        # A bare name from a screenshot: take the higher-scoring player, and say so.
        best = max(hits, key=lambda p: p.get("total_points_season") or 0)
        print(f"  note: {name!r} is ambiguous ({len(hits)} players); using "
              f"{best['web_name']} ({best['team']}). Add ' (TEAM)' to be sure.")
        return best["player_id"]
    if len(hits) != 1:
        raise SystemExit(f"cannot resolve {name!r} ({len(hits)} matches)")
    return hits[0]["player_id"]


def valid(xi, pos):
    c = {p: sum(pos[i] == p for i in xi) for p in ("GKP", "DEF", "MID", "FWD")}
    return c["GKP"] == 1 and c["DEF"] >= 3 and c["FWD"] >= 1


def score_team(team, gw, con, hits=0):
    players = con.execute("""select player_id, web_name, current_team as team, position_code,
                                    total_points_season
                             from main.mart_player_current""").df().to_dict("records")
    pos = {p["player_id"]: p["position_code"] for p in players}
    gwpts = {r[0]: (int(r[1]), int(r[2])) for r in con.execute("""
        select player_id, sum(total_points), sum(minutes) from main.fct_player_fixtures
        where gameweek = ? group by player_id""", [gw]).fetchall()}
    pts = lambda i: gwpts.get(i, (0, 0))[0]
    mins = lambda i: gwpts.get(i, (0, 0))[1]

    xi = [resolve(n, players) for n in team["starting"]]
    bench = [resolve(n, players) for n in team["bench"]]
    subs = []
    for b in bench:
        if mins(b) == 0:
            continue
        for out in [i for i in xi if mins(i) == 0]:
            trial = [b if i == out else i for i in xi]
            if (pos[b] == "GKP") == (pos[out] == "GKP") and valid(trial, pos):
                xi = trial
                subs.append((out, b))
                break
    cap, vice = resolve(team["captain"], players), resolve(team["vice_captain"], players)
    armband = cap if mins(cap) > 0 else (vice if mins(vice) > 0 and vice in xi else None)
    total = sum(pts(i) for i in xi) + (pts(armband) if armband else 0) - hits
    name = {p["player_id"]: f"{p['web_name']} ({p['team']})" for p in players}
    return {"total": total, "xi": [(name[i], pts(i)) for i in xi],
            "armband": name.get(armband), "auto_subs": [(name[o], name[i]) for o, i in subs],
            "hits": hits}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, required=True)
    ap.add_argument("teams", nargs="+")
    ap.add_argument("--hits", type=int, nargs="*", default=[])
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    done = con.execute("select is_data_checked from main.stg_fpl__gameweeks where gameweek = ?",
                       [a.gw]).fetchone()
    if not done or not done[0]:
        print(f"warning: GW{a.gw} is not data_checked yet; points may still change")
    for k, path in enumerate(a.teams):
        team = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        r = score_team(team, a.gw, con, a.hits[k] if k < len(a.hits) else 0)
        print(f"{r['total']:>4}  {path}  armband: {r['armband']}  auto-subs: {r['auto_subs'] or '-'}"
              f"{'  hits: -' + str(r['hits']) if r['hits'] else ''}")


if __name__ == "__main__":
    main()
