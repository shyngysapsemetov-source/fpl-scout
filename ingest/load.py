"""Load dated raw folders written by fetch.py into the `raw` schema.

Usage:  python ingest/load.py [--target dev|prod] [--raw data/raw] [--dt YYYY-MM-DD | --all]

Sample pulls (fetch.py --limit) land in data/raw_sample/; load them with --raw data/raw_sample,
dev only (D21).

The JSON is stored as it landed (D16); staging does the unpacking. Each table gets one snapshot
per date: loading a date again replaces that date's rows for every file present in the folder,
in one transaction (D17). A light day has no element_summaries file, so that table is untouched.
"""
import argparse
import datetime as dt
import pathlib
import sys

from db import connect

# table -> (file, select producing the table's columns from that file)
LOADS = {
    "bootstrap_static": ("bootstrap_static.json", """
        select ?::date, (content::json->>'_fetched_at')::timestamptz, content::json
        from read_text(?)"""),
    "fixtures": ("fixtures.json", """
        select ?::date, (content::json->>'_fetched_at')::timestamptz,
               content::json->'fixtures'
        from read_text(?)"""),
    "element_summaries": ("element_summaries.jsonl", """
        select ?::date, _fetched_at::timestamptz, element_id, history
        from read_json(?, format = 'newline_delimited',
                       columns = {element_id: 'INTEGER', _fetched_at: 'VARCHAR',
                                  history: 'JSON'})"""),
}


def load_folder(con, folder):
    day = folder.name.removeprefix("dt=")
    dt.date.fromisoformat(day)  # fail loudly on a malformed folder name
    loaded = []
    con.execute("begin")
    try:
        for table, (fname, select) in LOADS.items():
            f = folder / fname
            if not f.exists():
                continue
            con.execute(f"delete from raw.{table} where dt = ?", [day])
            con.execute(f"insert into raw.{table} {select}", [day, f.as_posix()])
            n = con.execute(f"select count(*) from raw.{table} where dt = ?", [day]).fetchone()[0]
            loaded.append(f"{table}={n}")
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    print(f"loaded {folder.name}: {', '.join(loaded) or 'nothing'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["dev", "prod"], default="dev")
    ap.add_argument("--raw", default="data/raw")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dt", help="date folder to load (default: today, UTC)")
    g.add_argument("--all", action="store_true", help="every dated folder under --raw")
    args = ap.parse_args()

    raw = pathlib.Path(args.raw)
    if args.target == "prod" and "sample" in raw.resolve().name:
        sys.exit(f"refusing to load sample pulls from {raw} into prod (D21)")
    if args.all:
        folders = sorted(raw.glob("dt=*"))
    else:
        day = args.dt or dt.datetime.now(dt.timezone.utc).date().isoformat()
        folders = [raw / f"dt={day}"]
    missing = [f for f in folders if not f.is_dir()]
    if missing or not folders:
        sys.exit(f"no raw folder: {missing or raw}")

    con = connect(args.target)
    for folder in folders:
        load_folder(con, folder)


if __name__ == "__main__":
    main()
