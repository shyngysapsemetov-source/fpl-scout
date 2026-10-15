"""Pull the FPL API into dated raw files.

Usage:  python ingest/fetch.py [--out data/raw] [--limit N]

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--limit", type=int, help="only the first N players (for testing)")
    args = ap.parse_args()

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

    ids = [e["id"] for e in bootstrap["elements"]][: args.limit]
    with open(out / "element_summaries.jsonl", "w", encoding="utf-8") as f:
        for i, pid in enumerate(ids, 1):
            summary = get(s, f"element-summary/{pid}/")
            f.write(json.dumps({"element_id": pid, "_fetched_at": fetched_at,
                                "history": summary["history"]}) + "\n")
            if i % 100 == 0:
                print(f"{i}/{len(ids)} players")
            time.sleep(PAUSE_S)

    print(f"wrote {out}: {len(bootstrap['elements'])} players, {len(fixtures)} fixtures, "
          f"{len(ids)} summaries")


if __name__ == "__main__":
    main()
