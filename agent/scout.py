"""fpl-scout CLI for agents (D73, D74): the app's views as text, every call logged.

Usage (from the repo root):
  python agent/scout.py rankings [--pos MID] [--team ARS] [--min-price 5] [--max-price 8.5]
                                 [--sort pts|value|form|xg|cs_next|...] [--desc] [--all] [--limit 30]
  python agent/scout.py report "Saka" [--section summary fixtures ...]   # scout card as text
  python agent/scout.py compare "Saka" "Palmer" "Salah"
  python agent/scout.py fixtures [--side attack|defence] [--team ARS] [--gw 11]
  python agent/scout.py validate team.json        # squad legality, cost, formation

It reads a frozen snapshot (default data/agent/gw10/dev.duckdb, or FPL_SCOUT_DB) so an
experiment can never see data from after its deadline. Calls are appended as JSON lines to
calls.jsonl next to the snapshot, for later analysis of how the tool was used.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys

import duckdb
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = pathlib.Path(os.environ.get("FPL_SCOUT_DB", ROOT / "data" / "agent" / "gw10" / "dev.duckdb"))
LOG = DB.parent / "calls.jsonl"
# Bump on every change to what the tool shows (D82), so each agent run records its version.
TOOL_VERSION = "2026-11-07.1"   # + expected points (xPts)
SORTS = {
    "pts": "points_per_match_season", "ppm": "points_per_match_season",
    "total": "total_points_season", "value": "points_per_million",
    "form": "points_per_match_l5_stable", "xg": "xg_per90_season", "xa": "xa_per90_season",
    "bps": "bps_per90_season", "defcon": "defensive_contribution_per90_season",
    "owned": "selected_by_pct", "price": "price", "saves": "saves_per90_season",
    "xgc": "xgc_per90_season", "cs_next": "next_gw_clean_sheet_prob",
    "xg_next": "next_gw_expected_goals_for", "xg_share": "xg_share_season",
    "xpts": "xpts_next_gw", "xpts5": "xpts_next5", "xpts5_value": "xpts_next5_per_million",
}
ASCENDING = {"xgc"}   # lower is better
SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def con():
    return duckdb.connect(str(DB), read_only=True)


def missing(v):
    """True for None, NaN and pandas NA (nullable integer columns)."""
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def f(v, spec):
    return "-" if missing(v) else format(v, spec)


def vp(r, col, spec):
    """Value with its position percentile, e.g. 0.42(95)."""
    v = getattr(r, col, None)
    p = getattr(r, col + "_pctl", None)
    return f(v, spec) if missing(p) else f"{f(v, spec)}({f(p, '.0f')})"


def set_pieces(r):
    tags = []
    for label, col in (("pen", "penalties_order"), ("cor", "corners_order"),
                       ("fk", "direct_freekicks_order")):
        v = getattr(r, col, None)
        if not missing(v):
            tags.append(f"{label}{int(v)}")
    return ",".join(tags) or "-"


def next_gw(r):
    if missing(getattr(r, "next_gameweek", None)):
        return "-"
    return (f"GW{int(r.next_gameweek)} {r.next_gw_opponents}: team xG {r.next_gw_expected_goals_for:.2f}, "
            f"xGA {r.next_gw_expected_goals_against:.2f}, CS {r.next_gw_clean_sheet_prob:.0%}")


def current(c):
    return c.execute("select * from main.mart_player_current").df()


def find(df, name):
    """Match 'Saka', 'saka', or 'Saka (ARS)'; ambiguous names list the candidates."""
    q = name.strip().lower()
    team = None
    if q.endswith(")") and "(" in q:
        q, team = q[:-1].split("(", 1)
        q, team = q.strip(), team.split(",")[0].strip().upper()
    hits = df[df.web_name.str.lower() == q]
    if hits.empty:
        hits = df[df.web_name.str.lower().str.contains(q, regex=False)
                  | (df.first_name.fillna("") + " " + df.second_name.fillna("")).str.lower()
                  .str.contains(q, regex=False)] if "first_name" in df else \
            df[df.web_name.str.lower().str.contains(q, regex=False)]
    if team:
        hits = hits[hits.current_team == team]
    if len(hits) == 1:
        return hits.iloc[0]
    if hits.empty:
        sys.exit(f"no player matches {name!r}")
    opts = ", ".join(f"{r.web_name} ({r.current_team}, {r.position_code})" for r in hits.itertuples())
    sys.exit(f"{name!r} is ambiguous: {opts}. Use 'Name (TEAM)'.")


def status_txt(r):
    if r.status == "a":
        return "available"
    chance = f", {int(r.chance_of_playing_next_pct)}% chance" if r.chance_of_playing_next_pct == r.chance_of_playing_next_pct and r.chance_of_playing_next_pct is not None else ""
    return f"status {r.status}{chance}: {r.news or ''}".strip()


# ------------------------------------------------------------------------------- rankings
def cmd_rankings(a):
    c = con()
    df = current(c)
    pos = a.pos.upper() if a.pos else None
    if pos:
        df = df[df.position_code == pos]
    if a.team:
        df = df[df.current_team == a.team.upper()]
    if a.max_price:
        df = df[df.price <= a.max_price]
    if a.min_price:
        df = df[df.price >= a.min_price]
    if not a.all:
        df = df[df.is_rank_eligible]
    last8 = c.execute("""select player_id, list(total_points order by kickoff_at) recent
        from (select *, row_number() over (partition by player_id order by kickoff_at desc) n
              from main.fct_player_rolling where is_played) where n <= 8 group by 1""").df()
    df = df.merge(last8, on="player_id", how="left")
    asc = a.sort in ASCENDING or (a.sort == "price" and not a.desc)
    df = df.sort_values(SORTS[a.sort], ascending=asc, na_position="last")
    keeper = pos == "GKP"
    # Position-aware columns: keepers get saves / xGC / clean-sheet chance instead of xG / xA.
    cols = ([("saves/90", "saves_per90_season", ".1f"), ("xGC/90", "xgc_per90_season", ".2f"),
             ("BPS/90", "bps_per90_season", ".1f")] if keeper else
            [("xG/90", "xg_per90_season", ".2f"), ("xA/90", "xa_per90_season", ".2f"),
             ("BPS/90", "bps_per90_season", ".1f")])
    print(f"fpl-scout {TOOL_VERSION} · RANKINGS pos={pos or 'all'} team={a.team or 'all'} "
          f"price={a.min_price or '-'}..{a.max_price or '-'} sort={a.sort} eligible_only={not a.all}  "
          f"({len(df)} players, showing {min(a.limit, len(df))})")
    print("xP GW / xP 5GW = expected FPL points next gameweek / next 5 (our model, backtested). "
          "value(pct): per-90 value with its 0-100 percentile within position (higher pct is better). "
          "pts/m = season points per £m. L5 = stabilised last-5 points per match. "
          "set = set-piece order (pen1 = first-choice penalties).")
    print(f"{'#':>3} {'player':<22}{'pos':<4}{'£m':>5} {'xP GW':>6} {'xP 5GW':>6} {'pts':>4} {'pts/mt':>6} {'L5':>5} {'pts/m':>5} "
          + "".join(f"{h:>11}" for h, _, _ in cols)
          + f" {'starts':>6} {'set':<15}{'next GW':<34}{'own%':>5}  last 8      status")
    for i, r in enumerate(df.head(a.limit).itertuples(), 1):
        last = " ".join(str(int(x)) for x in (list(r.recent) if r.recent is not None and not isinstance(r.recent, float) else []))
        ngw = "-" if missing(r.next_gameweek) else (
            f"{r.next_gw_opponents} {r.next_gw_expected_goals_for:.1f}xG/{r.next_gw_clean_sheet_prob:.0%}CS")
        print(f"{i:>3} {r.web_name[:15] + ' (' + r.current_team + ')':<22}{r.position_code:<4}"
              f"{r.price:>5.1f} {f(r.xpts_next_gw, '.2f'):>6} {f(r.xpts_next5, '.1f'):>6} "
              f"{f(r.total_points_season, '.0f'):>4} {f(r.points_per_match_season, '.2f'):>6} "
              f"{f(r.points_per_match_l5_stable, '.2f'):>5} {f(r.points_per_million, '.1f'):>5} "
              + "".join(f"{vp(r, col, spec):>11}" for _, col, spec in cols)
              + f" {f(r.starts_season, '.0f') + '/' + f(r.team_fixtures_season, '.0f'):>6} {set_pieces(r):<15}"
              f"{ngw:<34}{f(r.selected_by_pct, '.1f'):>5}  {last:<11} {'' if r.status == 'a' else status_txt(r)}")


# ------------------------------------------------------------------------------- report
SECTIONS = [
    ("Output", "Points per match", "points_per_match_season", ".2f", None),
    ("Output", "Last-5 ppm (raw)", "points_per_match_l5", ".2f", None),
    ("Attack", "xG per 90", "xg_per90_season", ".2f", None),
    ("Attack", "xA per 90", "xa_per90_season", ".2f", None),
    ("Bonus", "BPS per 90", "bps_per90_season", ".1f", None),
    ("Minutes", "Minutes per match", "minutes_per_match_season", ".0f", None),
    ("Minutes", "Played share, last 5", "played_share_l5", ".0%", None),
    ("Defence", "xGC per 90 (low is good)", "xgc_per90_season", ".2f", ("GKP", "DEF")),
    ("Defence", "Clean-sheet chance, next 5", "next5_clean_sheet_prob", ".0%", ("GKP", "DEF")),
    ("Defence", "Def. contribution per 90", "defensive_contribution_per90_season", ".1f", ("DEF", "MID", "FWD")),
    ("Defence", "Saves per 90", "saves_per90_season", ".1f", ("GKP",)),
]


def report(c, r):
    pid = int(r.player_id)
    out = []
    rank = r.points_per_match_season_rank
    pool = int(c.execute("select count(*) from main.mart_player_current where position_code = ? "
                         "and is_rank_eligible", [r.position_code]).fetchone()[0])
    out.append("##summary")
    out.append(f"fpl-scout {TOOL_VERSION} · === {r.web_name} · {r.current_team} · {r.position_code} · £{r.price:.1f}m · "
               f"owned {r.selected_by_pct:.1f}% · {status_txt(r)}")
    out.append(f"Season points {f(r.total_points_season, '.0f')} · pts/match {f(r.points_per_match_season, '.2f')} "
               f"({'#' + str(int(rank)) + ' of ' + str(pool) + ' ' + r.position_code + 's' if not missing(rank) else 'not ranked: under 40% of team minutes'}) · "
               f"form vs season {f((r.points_per_match_l5_stable or 0) - (r.points_per_match_season_stable or 0), '+.2f')} · "
               f"played last 5: {f(r.played_share_l5, '.0%')}, {f(r.avg_minutes_l5, '.0f')} min avg")
    out.append(f"Role: started {f(r.starts_season, '.0f')} of {f(r.team_fixtures_season, '.0f')} team fixtures, "
               f"{f(r.sub_appearances_season, '.0f')} sub appearances, {f(r.early_subs_season, '.0f')} starts ended "
               f"before 60', {f(r.zero_minute_fixtures_season, '.0f')} fixtures with 0' (the API gives no reason) · "
               f"set pieces: {set_pieces(r)} · share of team xG {f(r.xg_share_season, '.0%')}, "
               f"xA {f(r.xa_share_season, '.0%')} · {f(r.points_per_million, '.1f')} pts per £m")
    out.append(f"Next gameweek: {next_gw(r)}")
    out.append(f"Expected points: next GW {f(r.xpts_next_gw, '.2f')} (P(start) {f(r.p_start_next_gw, '.0%')}), "
               f"next 5 GWs {f(r.xpts_next5, '.1f')} ({f(r.xpts_next5_per_million, '.2f')} per £m)")
    xp = c.execute("""select gameweek, xpts, xp_appearance, xp_goals, xp_assists, xp_clean_sheet,
                             xp_goals_conceded, xp_saves, xp_bonus, xp_defensive_contribution
                      from main.fct_player_expected_points
                      where player_id = ? and is_upcoming order by kickoff_at""", [pid]).df()
    for x in xp.itertuples():
        out.append(f"  GW{x.gameweek}: {x.xpts:.2f} = app {x.xp_appearance:.2f} + goals {x.xp_goals:.2f} + "
                   f"assists {x.xp_assists:.2f} + CS {x.xp_clean_sheet:.2f} + GC {x.xp_goals_conceded:.2f} + "
                   f"saves {x.xp_saves:.2f} + bonus {x.xp_bonus:.2f} + DC {x.xp_defensive_contribution:.2f}")
    out.append("##percentiles")
    out.append("Percentiles within position (0-100, higher is better); value in brackets:")
    for sec, label, col, spec, positions in SECTIONS:
        if positions and r.position_code not in positions:
            continue
        p = getattr(r, col + "_pctl", None) if r.is_rank_eligible else None
        out.append(f"  {sec:<8} {label:<30} {f(p, '.0f'):>4}  ({f(getattr(r, col), spec)})")

    nxt = c.execute("""select gameweek, opponent, is_home, expected_goals_for, expected_goals_against,
                              clean_sheet_prob from main.fct_team_next_fixtures
                       where team_id = ? order by kickoff_at""", [int(r.current_team_id)]).df()
    out.append("##fixtures")
    out.append("Next fixtures (team expected goals for / against, clean-sheet chance):")
    for x in nxt.itertuples():
        out.append(f"  GW{x.gameweek} {x.opponent} ({'H' if x.is_home else 'A'}): "
                   f"xG for {x.expected_goals_for:.2f}, against {x.expected_goals_against:.2f}, CS {x.clean_sheet_prob:.0%}")

    roll = c.execute("""select gameweek, points_per_match_l5_stable l5, points_per_match_l10_stable l10,
                               points_per_match_season_stable ssn, xg_per90_l5_stable xg5,
                               xa_per90_l5_stable xa5 from main.fct_player_rolling
                        where player_id = ? order by kickoff_at""", [pid]).df()
    out.append("##form")
    if len(roll):
        out.append("Form by GW (stabilised ppm L5 / L10 / season; xG/90 L5; xA/90 L5):")
        out.append("  " + "  ".join(f"GW{x.gameweek}:{f(x.l5, '.1f')}/{f(x.l10, '.1f')}/{f(x.ssn, '.1f')}"
                                    for x in roll.itertuples()))
        out.append("  xG/90 L5: " + " ".join(f(v, ".2f") for v in roll.xg5) +
                   " | xA/90 L5: " + " ".join(f(v, ".2f") for v in roll.xa5))

    out.append("##matches")
    m = c.execute("""select f.gameweek, f.minutes, f.starts, f.total_points, f.goals_scored, f.assists,
                            f.clean_sheets, f.bonus, f.bps, f.defensive_contribution, f.saves, f.xg, f.xa,
                            o.team_short_name opp, f.was_home
                     from main.fct_player_fixtures f
                     join main.stg_fpl__teams o on o.team_id = f.opponent_team_id
                     where f.player_id = ? order by f.kickoff_at""", [pid]).df()
    out.append("Match by match (GW opp: pts | start/sub min G A CS bonus BPS DefCon saves xG xA):")
    for x in m.itertuples():
        out.append(f"  GW{x.gameweek} {x.opp}{'(H)' if x.was_home else '(A)'}: {x.total_points:>2} | "
                   f"{'start' if x.starts else ('sub  ' if x.minutes else '-    ')} {x.minutes:>2}' G{x.goals_scored} A{x.assists} CS{x.clean_sheets} B{x.bonus} "
                   f"BPS{x.bps} DC{x.defensive_contribution} S{x.saves} xG{x.xg:.2f} xA{x.xa:.2f}")
    out.append("##points")
    played = m[m.minutes > 0]
    if len(played):
        out.append(f"Goals vs xG: {played.goals_scored.sum()} vs {played.xg.sum():.1f} · "
                   f"assists vs xA: {played.assists.sum()} vs {played.xa.sum():.1f}")
    br = c.execute("""select sum(appearance_points) app, sum(goal_points) g, sum(assist_points) a,
                             sum(clean_sheet_points) cs, sum(bonus_points) b,
                             sum(defensive_contribution_points) dc, sum(save_points + penalty_save_points) sv,
                             sum(goals_conceded_points + yellow_card_points + red_card_points
                                 + own_goal_points + penalty_miss_points) neg
                      from main.int_player_fixture_points where player_id = ?""", [pid]).df().iloc[0]
    out.append(f"Season points by category: appearance {br.app:.0f}, goals {br.g:.0f}, assists {br.a:.0f}, "
               f"clean sheets {br.cs:.0f}, bonus {br.b:.0f}, def. contribution {br.dc:.0f}, saves {br.sv:.0f}, "
               f"deductions {br.neg:.0f}")
    return "\n".join(out)


def cmd_report(a):
    c = con()
    text = report(c, find(current(c), a.name))
    keep, out = True, []
    for line in text.splitlines():
        if line.startswith("##"):
            keep = not a.section or line[2:] in a.section
            continue
        if keep:
            out.append(line)
    print("\n".join(out))


def cmd_compare(a):
    c = con()
    df = current(c)
    rows = [find(df, n) for n in a.names[:5]]
    cols = [("Team", lambda r: r.current_team), ("Pos", lambda r: r.position_code),
            ("Price", lambda r: f(r.price, ".1f")), ("Season pts", lambda r: f(r.total_points_season, ".0f")),
            ("Pts/match", lambda r: f(r.points_per_match_season, ".2f")),
            ("Pts per £m", lambda r: f(r.points_per_million, ".1f")),
            ("xPts next GW", lambda r: f(r.xpts_next_gw, ".2f")),
            ("xPts next 5", lambda r: f(r.xpts_next5, ".1f")),
            ("L5 ppm (stab.)", lambda r: f(r.points_per_match_l5_stable, ".2f")),
            ("xG/90", lambda r: f(r.xg_per90_season, ".2f")), ("xA/90", lambda r: f(r.xa_per90_season, ".2f")),
            ("BPS/90", lambda r: f(r.bps_per90_season, ".1f")),
            ("DefCon/90", lambda r: f(r.defensive_contribution_per90_season, ".1f")),
            ("Mins/match", lambda r: f(r.minutes_per_match_season, ".0f")),
            ("Played L5", lambda r: f(r.played_share_l5, ".0%")),
            ("Starts", lambda r: f(r.starts_season, ".0f") + "/" + f(r.team_fixtures_season, ".0f")),
            ("Early subs", lambda r: f(r.early_subs_season, ".0f")),
            ("Set pieces", set_pieces),
            ("xG share", lambda r: f(r.xg_share_season, ".0%")),
            ("Next GW opp", lambda r: (r.next_gw_opponents or "-")[:15]),
            ("Next GW xG for", lambda r: f(r.next_gw_expected_goals_for, ".2f")),
            ("Next GW CS", lambda r: f(r.next_gw_clean_sheet_prob, ".0%")),
            ("Next5 xG for", lambda r: f(r.next5_expected_goals_for, ".2f")),
            ("Next5 CS", lambda r: f(r.next5_clean_sheet_prob, ".0%")),
            ("Owned %", lambda r: f(r.selected_by_pct, ".1f")), ("Status", lambda r: r.status)]
    print(f"fpl-scout {TOOL_VERSION} · COMPARE")
    print(f"{'':<16}" + "".join(f"{r.web_name[:14]:>16}" for r in rows))
    for label, fn in cols:
        print(f"{label:<16}" + "".join(f"{fn(r):>16}" for r in rows))


def cmd_fixtures(a):
    c = con()
    fx = c.execute("select * from main.fct_team_next_fixtures order by team, kickoff_at").df()
    if a.team:
        fx = fx[fx.team == a.team.upper()]
    if a.gw:
        fx = fx[fx.gameweek == a.gw]
    attack = a.side == "attack"
    key = "expected_goals_for" if attack else "clean_sheet_prob"
    order = fx.groupby("team")[key].mean().sort_values(ascending=False).index
    gws = sorted(fx.gameweek.unique())
    what = ("team expected goals SCORED (good for its attackers)" if attack
            else "clean-sheet chance and expected goals CONCEDED (good for its defenders and keeper)")
    print(f"fpl-scout {TOOL_VERSION} · FIXTURES GW{gws[0]}-GW{gws[-1]} · {what} · "
          f"sorted by average {'xG for' if attack else 'clean-sheet chance'}")
    print(f"{'team':<6}" + "".join(f"{'GW' + str(g):<16}" for g in gws) + ("avg xG for" if attack else "avg CS"))
    for t in order:
        tf = fx[fx.team == t]
        cells = []
        for g in gws:
            games = tf[tf.gameweek == g]
            cells.append(" + ".join(
                (f"{x.opponent}{'H' if x.is_home else 'A'} {x.expected_goals_for:.2f}" if attack else
                 f"{x.opponent}{'H' if x.is_home else 'A'} {x.clean_sheet_prob:.0%}/{x.expected_goals_against:.1f}")
                for x in games.itertuples()) or "BLANK")
        avg = tf[key].mean()
        print(f"{t:<6}" + "".join(f"{cell:<16}" for cell in cells) + (f"{avg:.2f}" if attack else f"{avg:.0%}"))


# ------------------------------------------------------------------------------- validate
def cmd_validate(a):
    """team.json: {"squad": [15 names], "starting": [11 names], "bench": [4 names, GK first
    then outfield in auto-sub order], "captain": name, "vice_captain": name}."""
    c = con()
    df = current(c)
    team = json.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))
    errs = []
    squad = [find(df, n) for n in team["squad"]]
    ids = [int(r.player_id) for r in squad]
    if len(set(ids)) != 15:
        errs.append(f"squad must be 15 different players (got {len(set(ids))})")
    counts = {p: sum(r.position_code == p for r in squad) for p in SQUAD}
    for p, n in SQUAD.items():
        if counts[p] != n:
            errs.append(f"need {n} {p}, have {counts[p]}")
    clubs = {}
    for r in squad:
        clubs[r.current_team] = clubs.get(r.current_team, 0) + 1
    errs += [f"max 3 per club: {t} has {n}" for t, n in clubs.items() if n > 3]
    cost = sum(r.price for r in squad)
    if cost > a.budget + 1e-9:
        errs.append(f"over budget: £{cost:.1f}m > £{a.budget:.1f}m")
    start = [int(find(df, n).player_id) for n in team["starting"]]
    bench = [int(find(df, n).player_id) for n in team["bench"]]
    if len(start) != 11 or len(bench) != 4 or set(start) | set(bench) != set(ids) or set(start) & set(bench):
        errs.append("starting (11) and bench (4) must split the squad exactly")
    pos = {int(r.player_id): r.position_code for r in squad}
    xi = [pos.get(i) for i in start]
    if xi.count("GKP") != 1 or xi.count("DEF") < 3 or xi.count("FWD") < 1:
        errs.append(f"invalid formation: {xi.count('GKP')} GK, {xi.count('DEF')} DEF, "
                    f"{xi.count('MID')} MID, {xi.count('FWD')} FWD (need 1 GK, 3+ DEF, 1+ FWD)")
    if bench and pos.get(bench[0]) != "GKP":
        errs.append("bench slot 1 must be the reserve goalkeeper")
    cap, vice = int(find(df, team["captain"]).player_id), int(find(df, team["vice_captain"]).player_id)
    if cap not in start or vice not in start or cap == vice:
        errs.append("captain and vice must be different players in the starting XI")
    print(f"cost £{cost:.1f}m of £{a.budget:.1f}m (bank £{a.budget - cost:.1f}m) · "
          f"formation {xi.count('DEF')}-{xi.count('MID')}-{xi.count('FWD')}")
    print("VALID" if not errs else "INVALID:\n  " + "\n  ".join(errs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rankings")
    r.add_argument("--pos", choices=["GKP", "DEF", "MID", "FWD", "gkp", "def", "mid", "fwd"])
    r.add_argument("--team")
    r.add_argument("--max-price", type=float)
    r.add_argument("--min-price", type=float)
    r.add_argument("--desc", action="store_true", help="with --sort price: most expensive first")
    r.add_argument("--sort", choices=list(SORTS), default="ppm")
    r.add_argument("--all", action="store_true", help="include players under 40%% of team minutes")
    r.add_argument("--limit", type=int, default=30)
    rp = sub.add_parser("report")
    rp.add_argument("name")
    rp.add_argument("--section", nargs="+",
                    choices=["summary", "percentiles", "fixtures", "form", "matches", "points"],
                    help="show only these parts of the report")
    cp = sub.add_parser("compare")
    cp.add_argument("names", nargs="+")
    fx = sub.add_parser("fixtures")
    fx.add_argument("--side", choices=["attack", "defence"], default="attack")
    fx.add_argument("--team")
    fx.add_argument("--gw", type=int, help="one gameweek only")
    v = sub.add_parser("validate")
    v.add_argument("file")
    v.add_argument("--budget", type=float, default=102.0)
    a = ap.parse_args()
    if not DB.exists():
        sys.exit(f"snapshot not found: {DB}")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                             "tool_version": TOOL_VERSION, "argv": sys.argv[1:]}) + "\n")
    {"rankings": cmd_rankings, "report": cmd_report, "compare": cmd_compare,
     "fixtures": cmd_fixtures, "validate": cmd_validate}[a.cmd](a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
