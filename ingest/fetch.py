"""Pull the FPL API into dated raw files.

Usage:  python ingest/fetch.py [--mode auto|light|full] [--target dev|prod] [--out data/raw]
                               [--limit N]

--limit N pulls only N players and writes to data/raw_sample/, never data/raw/ (D21).

Two pulls (D10). The light pull is two calls: bootstrap-static and fixtures. The full pull adds
one element-summary call per player (~800). `auto` does the full pull only when today's latest
`data_checked` gameweek is newer than the one recorded at the last full pull, read from the raw
schema of --target (D18). A failed full pull loads nothing, so the next day retries it.

Writes one folder per run date, never overwriting an earlier date:
    data/raw/dt=YYYY-MM-DD/bootstrap_static.json   players, teams, gameweeks (current state)
    data/raw/dt=YYYY-MM-DD/fixtures.json           every fixture this season
    data/raw/dt=YYYY-MM-DD/element_summaries.jsonl one line per player: per-fixture history

The per-fixture history is complete for the season on every run, so any single day rebuilds the
stats. Price, ownership and status are only ever current, so their history is the sequence of
dated folders, which is why a day is never overwritten.

The API is public but unofficial and unlicensed: one request at a time, a pause between calls,
backoff on 5xx (it returns 503 while a gameweek is being updated).
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import time

import requests

from db import connect

BASE = "https://fantasy.premierleague.com/api"
PAUSE_S = 0.5
HEADERS = {"User-Agent": "fpl-scout (https://github.com/shyngysapsemetov-source/fpl-scout)"}


def get(session, path, tries=5):
    for attempt in range(tries):
        r = session.get(f"{BASE}/{path}", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code >= 500 or r.status_code == 429:
            wait = 2 ** attempt * 5
            print(f"{path}: HTTP {r.status_code}, retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        r.raise_for_status()
    sys.exit(f"{path}: gave up after {tries} tries")


def latest_checked_gw(events):
    return max((e["id"] for e in events if e["data_checked"]), default=0)


def needs_full(target, today_checked):
    """True when today's latest data_checked GW is newer than at the last full pull."""
    con = connect(target, read_only=True)
    if con is None:
        return True, "no database yet"
    has_raw = con.execute("""select count(*) from information_schema.tables
                             where table_schema = 'raw' and table_name = 'element_summaries'
                          """).fetchone()[0]
    last = has_raw and con.execute("""
        with last_full as (select max(dt) as dt from raw.element_summaries)
        , events as (
            select unnest(cast(b.payload->'events' as json[])) as ev
            from raw.bootstrap_static b join last_full using (dt))
        select max((ev->>'id')::int) from events
        where (ev->>'data_checked')::boolean""").fetchone()[0]
    con.close()
    if not last:
        return True, "no full pull on record"
    if today_checked > last:
        return True, f"GW{today_checked} data_checked, last full pull had GW{last}"
    return False, f"no new data_checked GW (still GW{last})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="default data/raw, or data/raw_sample with --limit (D21)")
    ap.add_argument("--mode", choices=["auto", "light", "full"], default="auto")
    ap.add_argument("--target", choices=["dev", "prod"], default="dev",
                    help="database whose raw schema `auto` checks")
    ap.add_argument("--limit", type=int, help="only the first N players (for testing)")
    args = ap.parse_args()
    # A sample must never become the latest full pull in a real history.
    args.out = args.out or ("data/raw_sample" if args.limit else "data/raw")

    run_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    out = pathlib.Path(args.out) / f"dt={run_date}"
    out.mkdir(parents=True, exist_ok=True)
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    s = requests.Session()
    bootstrap = get(s, "bootstrap-static/")
    bootstrap["_fetched_at"] = fetched_at
    (out / "bootstrap_static.json").write_text(json.dumps(bootstrap), encoding="utf-8")

    fixtures = get(s, "fixtures/")
    (out / "fixtures.json").write_text(
        json.dumps({"_fetched_at": fetched_at, "fixtures": fixtures}), encoding="utf-8")

    if args.mode == "auto":
        full, why = needs_full(args.target, latest_checked_gw(bootstrap["events"]))
    else:
        full, why = args.mode == "full", f"--mode {args.mode}"
    print(f"{'full' if full else 'light'} pull: {why}")
    if not full:
        print(f"wrote {out}: {len(bootstrap['elements'])} players, {len(fixtures)} fixtures")
        return

    ids = [e["id"] for e in bootstrap["elements"]][: args.limit]
    # Written under a temp name and renamed at the end, so a pull that dies halfway never
    # leaves a partial file for load.py to pick up.
    part = out / "element_summaries.jsonl.part"
    with open(part, "w", encoding="utf-8") as f:
        for i, pid in enumerate(ids, 1):
            summary = get(s, f"element-summary/{pid}/")
            f.write(json.dumps({"element_id": pid, "_fetched_at": fetched_at,
                                "history": summary["history"]}) + "\n")
            if i % 100 == 0:
                print(f"{i}/{len(ids)} players")
            time.sleep(PAUSE_S)
    part.replace(out / "element_summaries.jsonl")

    print(f"wrote {out}: {len(bootstrap['elements'])} players, {len(fixtures)} fixtures, "
          f"{len(ids)} summaries")


if __name__ == "__main__":
    main()
