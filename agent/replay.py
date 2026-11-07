"""Week-by-week season replay for an agent (D103).

Usage:
  python agent/replay.py prepare N          # as-of GW N rebuild + the agent's state file
  python agent/replay.py apply N            # validate decision_gwN.json, score GW N, roll state
  python agent/replay.py summary            # points by GW and total

Rules (standard FPL 2026/27, as best understood): GW1 squad from £100.0m; 1 free transfer per
GW after GW1, unused ones roll over up to 5; each extra transfer costs 4 points; selling price =
purchase price + half of any rise (rounded down to £0.1m), a fall is taken in full; squad 2/5/5/3,
max 3 per club; XI 1 GK, 3+ DEF, 1+ FWD; auto-subs in bench order; captain x2 (vice if the
captain plays 0 minutes). Chips, each once in GW1-19, at most one per GW: wildcard (unlimited
free transfers, permanent), free_hit (unlimited transfers for one GW, squad reverts after),
bench_boost (bench points count), triple_captain (captain x3). Free transfers are kept, not
used, through a wildcard or free hit week.

Files under data/agent/replay/: gwN/dev.duckdb (the as-of tool), state_gwN.json (what the
agent starts GW N with), decision_gwN.json (the agent's choice), result_gwN.json (score).
Actual points come from data/dev.duckdb (all played gameweeks).
"""
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "agent" / "replay"
FULL_DB = ROOT / "data" / "dev.duckdb"
DBT = ROOT / ".venv" / "Scripts" / "dbt"
QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
CHIPS = ["wildcard", "free_hit", "bench_boost", "triple_captain"]


def db_for(n):
    return DIR / f"gw{n}" / "dev.duckdb"


def players_at(n):
    """Every player available at the GW n deadline, with his price then."""
    con = duckdb.connect(str(db_for(n)), read_only=True)
    df = con.execute("""select player_id, web_name, current_team as team, position_code as pos, price
                        from main.mart_player_current""").df()
    con.close()
    df["label"] = df.web_name + " (" + df.team + ")"
    return df.set_index("player_id")


def resolve(df, name):
    hit = df[df.label == name]
    if hit.empty:
        hit = df[df.web_name == name]
    if len(hit) != 1:
        raise SystemExit(f"cannot resolve {name!r} at this deadline ({len(hit)} matches); use 'Name (TEAM)'")
    return int(hit.index[0])


def selling_price(bought, now):
    if now <= bought:
        return now
    return round(bought + math.floor(round((now - bought) * 10) / 2) / 10, 1)


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def save(path, obj):
    pathlib.Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------------- prepare
def prepare(n):
    target = db_for(n)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    shutil.copy(FULL_DB, target)
    env = {**os.environ, "FPL_REPLAY_DB": str(target)}
    subprocess.run([str(DBT), "run", "--profiles-dir", ".", "--target", "replay", "-q",
                    "--vars", json.dumps({"as_of_gw": n})], cwd=ROOT / "dbt", env=env, check=True)
    prev = DIR / f"state_gw{n}.json"
    if n == 1 and not prev.exists():
        state = {"gameweek": 1, "squad": [], "bank": 100.0, "free_transfers": None,
                 "chips_available": CHIPS[:], "note": "GW1: pick a fresh 15 from £100.0m (no transfers, no hits)"}
    else:
        state = load(prev)
    # Refresh current prices and selling prices at this deadline.
    df = players_at(n)
    for p in state["squad"]:
        now = float(df.at[p["player_id"], "price"]) if p["player_id"] in df.index else p["bought"]
        p["price_now"] = now
        p["selling_price"] = selling_price(p["bought"], now)
    state["team_json_for_optimise"] = {
        "squad": [p["label"] for p in state["squad"]],
        "selling_prices": {p["label"]: p["selling_price"] for p in state["squad"]}}
    save(prev, state)
    print(f"prepared GW{n}: tool at {target}; state at {prev}")


# --------------------------------------------------------------------------------- apply
def score(team_ids, xi, bench, captain, vice, chip, n, pos):
    con = duckdb.connect(str(FULL_DB), read_only=True)
    pts = {r[0]: (int(r[1]), int(r[2])) for r in con.execute(
        "select player_id, sum(total_points), sum(minutes) from main.fct_player_fixtures "
        "where gameweek = ? group by 1", [n]).fetchall()}
    con.close()
    p = lambda i: pts.get(i, (0, 0))[0]
    m = lambda i: pts.get(i, (0, 0))[1]

    def valid(ids):
        c = {k: sum(pos[i] == k for i in ids) for k in QUOTA}
        return c["GKP"] == 1 and c["DEF"] >= 3 and c["FWD"] >= 1

    xi = list(xi)
    subs = []
    if chip != "bench_boost":
        for b in bench:
            if m(b) == 0:
                continue
            for out in [i for i in xi if m(i) == 0]:
                trial = [b if i == out else i for i in xi]
                if (pos[b] == "GKP") == (pos[out] == "GKP") and valid(trial):
                    xi, _ = trial, subs.append((out, b))
                    break
        counted = xi
    else:
        counted = xi + list(bench)
    armband = captain if m(captain) > 0 else (vice if m(vice) > 0 else None)
    mult = 3 if chip == "triple_captain" else 2
    total = sum(p(i) for i in counted) + (mult - 1) * (p(armband) if armband else 0)
    return total, subs, armband, {i: p(i) for i in counted}


def apply(n):
    state = load(DIR / f"state_gw{n}.json")
    d = load(DIR / f"decision_gw{n}.json")
    df = players_at(n)
    chip = d.get("chip")
    if chip and chip not in state["chips_available"]:
        raise SystemExit(f"chip {chip!r} not available: {state['chips_available']}")
    current = {p["player_id"]: p for p in state["squad"]}
    bank = state["bank"]
    fresh = n == 1 and not current
    if fresh or chip in ("wildcard", "free_hit") or "squad" in d and not d.get("transfers"):
        new_ids = [resolve(df, x) for x in d["squad"]]
        n_transfers = 0 if fresh else len(set(new_ids) - set(current))
        free_cost = True
    else:
        outs = [resolve(df, t["out"]) for t in d.get("transfers", [])]
        ins = [resolve(df, t["in"]) for t in d.get("transfers", [])]
        if any(o not in current for o in outs):
            raise SystemExit("transfer out of a player not in the squad")
        new_ids = [i for i in current if i not in outs] + ins
        n_transfers = len(ins)
        free_cost = False
    # Money: sell at selling prices, buy at this deadline's prices.
    budget = (100.0 if fresh else bank + sum(p["selling_price"] for p in current.values()))
    kept = [i for i in new_ids if i in current]
    bought = [i for i in new_ids if i not in current]
    cost = sum(current[i]["selling_price"] for i in kept) + sum(float(df.at[i, "price"]) for i in bought)
    errs = []
    if cost > budget + 1e-6:
        errs.append(f"over budget: £{cost:.1f}m > £{budget:.1f}m")
    pos = {i: df.at[i, "pos"] for i in new_ids}
    for k, q in QUOTA.items():
        if sum(pos[i] == k for i in new_ids) != q:
            errs.append(f"need {q} {k}")
    teams = {}
    for i in new_ids:
        teams[df.at[i, "team"]] = teams.get(df.at[i, "team"], 0) + 1
    errs += [f"max 3 per club: {t}" for t, c in teams.items() if c > 3]
    if len(set(new_ids)) != 15:
        errs.append("squad must be 15 different players")
    xi = [resolve(df, x) for x in d["starting"]]
    bench = [resolve(df, x) for x in d["bench"]]
    if set(xi) | set(bench) != set(new_ids) or len(xi) != 11 or len(bench) != 4:
        errs.append("starting XI + bench must be exactly the squad")
    cap, vice = resolve(df, d["captain"]), resolve(df, d["vice_captain"])
    if cap not in xi or vice not in xi or cap == vice:
        errs.append("captain and vice must be different starters")
    if errs:
        raise SystemExit("INVALID decision:\n  " + "\n  ".join(errs))
    ft = state["free_transfers"] or 0
    hits = 0 if (fresh or free_cost) else max(0, n_transfers - ft) * 4
    total, subs, armband, by_player = score(new_ids, xi, bench, cap, vice, chip, n, pos)
    net = total - hits
    label = lambda i: df.at[i, "label"]
    result = {"gameweek": n, "points": total, "hits": hits, "net": net, "chip": chip,
              "transfers": n_transfers, "armband": label(armband) if armband else None,
              "auto_subs": [(label(o), label(i)) for o, i in subs],
              "player_points": {label(i): v for i, v in by_player.items()},
              "bank_after": round(budget - cost, 1)}
    save(DIR / f"result_gw{n}.json", result)

    # Next state: the free hit squad reverts; otherwise the new squad stands.
    if chip == "free_hit":
        nxt_squad, nxt_bank = state["squad"], bank
    else:
        nxt_squad = [current[i] if i in current else
                     {"player_id": i, "label": label(i), "pos": pos[i], "bought": float(df.at[i, "price"])}
                     for i in new_ids]
        nxt_bank = round(budget - cost, 1)
    if fresh:
        nxt_ft = 1
    elif chip in ("wildcard", "free_hit"):
        nxt_ft = min(5, ft + 1)
    else:
        nxt_ft = min(5, max(0, ft - n_transfers) + 1)
    chips = [c for c in state["chips_available"] if c != chip]
    for p in nxt_squad:
        for k in ("price_now", "selling_price"):
            p.pop(k, None)
    save(DIR / f"state_gw{n + 1}.json", {"gameweek": n + 1, "squad": nxt_squad, "bank": nxt_bank,
                                          "free_transfers": nxt_ft, "chips_available": chips})
    print(f"GW{n}: {total} pts{f' - {hits} hit' if hits else ''} = {net}"
          f"{f' [{chip}]' if chip else ''} · armband {result['armband']} · auto-subs {result['auto_subs'] or '-'} "
          f"· transfers {n_transfers} · bank £{result['bank_after']}m · next GW free transfers {nxt_ft}")


def summary():
    rows = [load(p) for p in sorted(DIR.glob("result_gw*.json"), key=lambda p: int(p.stem[9:]))]
    tot = 0
    for r in rows:
        tot += r["net"]
        print(f"GW{r['gameweek']}: {r['net']:>3} (total {tot}){'  ' + r['chip'] if r['chip'] else ''}"
              f"{'  hit -' + str(r['hits']) if r['hits'] else ''}  armband {r['armband']}")
    print("TOTAL", tot)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1]
    if cmd == "prepare":
        prepare(int(sys.argv[2]))
    elif cmd == "apply":
        apply(int(sys.argv[2]))
    else:
        summary()
