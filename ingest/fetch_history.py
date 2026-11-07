"""Load past seasons from the vaastav/Fantasy-Premier-League archive into the raw schema (D43, D49).

Usage:  python ingest/fetch_history.py [--target dev|prod] [--seasons 2023-24 2024-25 2025-26]
                                       [--force]

The archive is MIT-licensed and asks for a credit link (see README). The data underneath is
the FPL API's, so the same rule applies as for our own pulls: it lands in the raw schema at
build time and is never committed or republished.

Past seasons don't change, so a season that is already loaded is skipped unless --force.
Every column is stored as text (as landed, D16); staging casts.
"""
import argparse
import pathlib
import sys
import tempfile
import time

import requests

from db import connect

BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
HEADERS = {"User-Agent": "fpl-scout (https://github.com/shyngysapsemetov-source/fpl-scout)"}
FILES = {  # raw table -> path inside a season folder
    "history_player_gw": "gws/merged_gw.csv",
    "history_teams": "teams.csv",
    "history_fixtures": "fixtures.csv",
    # element id -> stable player `code`, to link a player across seasons (D91)
    "history_players_raw": "players_raw.csv",
}


def download(url, dest):
    for attempt in range(4):
        r = requests.get(url, headers=HEADERS, timeout=60)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return
        time.sleep(2 ** attempt * 5)
    sys.exit(f"{url}: HTTP {r.status_code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["dev", "prod"], default="dev")
    ap.add_argument("--seasons", nargs="+", default=["2023-24", "2024-25", "2025-26"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    con = connect(args.target)
    with tempfile.TemporaryDirectory() as tmp:
        for season in args.seasons:
            for table, path in FILES.items():
                exists = con.execute(
                    "select count(*) from information_schema.tables "
                    "where table_schema = 'raw' and table_name = ?", [table]).fetchone()[0]
                if exists and not args.force and con.execute(
                        f"select count(*) from raw.{table} where season = ?",
                        [season]).fetchone()[0]:
                    print(f"{season} {table}: already loaded")
                    continue
                f = pathlib.Path(tmp) / f"{season}_{table}.csv"
                download(f"{BASE}/{season}/{path}", f)
                rel = f"read_csv(?, all_varchar = true, header = true)"
                con.execute("begin")
                if not exists:
                    con.execute(f"create table raw.{table} as "
                                f"select ?::varchar as season, * from {rel} limit 0",
                                [season, f.as_posix()])
                # Columns differ between seasons (e.g. defensive_contribution from 2025-26):
                # add any the table lacks, so older and newer seasons share one table.
                have = {r[0] for r in con.execute(f"describe raw.{table}").fetchall()}
                for (col,) in con.execute(f"select column_name from (describe select * from "
                                          f"read_csv('{f.as_posix()}', all_varchar = true, "
                                          f"header = true))").fetchall():
                    if col not in have:
                        con.execute(f'alter table raw.{table} add column "{col}" varchar')
                con.execute(f"delete from raw.{table} where season = ?", [season])
                # by name: columns differ between seasons (e.g. defensive_contribution from 2025-26)
                con.execute(f"insert into raw.{table} by name "
                            f"select ?::varchar as season, * from {rel}", [season, f.as_posix()])
                con.execute("commit")
                n = con.execute(f"select count(*) from raw.{table} where season = ?",
                                [season]).fetchone()[0]
                print(f"{season} {table}: loaded {n} rows")


if __name__ == "__main__":
    main()
