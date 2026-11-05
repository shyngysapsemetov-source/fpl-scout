"""Fail the run when the target database passes a size limit (D22).

Usage:  python ingest/storage_guard.py [--target dev|prod] [--max-gb 5]

MotherDuck Lite includes 10 GB and bills storage beyond it to the card on file. The 5 GB
default trips at half of that, long before any charge.
"""
import argparse
import re
import sys

from db import TARGETS, connect

UNITS = {"bytes": 1, "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40,
         "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12}


def to_bytes(text):
    m = re.fullmatch(r"\s*([\d.]+)\s*([a-z]+)\s*", str(text).lower())
    if not m or m.group(2) not in UNITS:
        sys.exit(f"cannot parse database size {text!r}")
    return float(m.group(1)) * UNITS[m.group(2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["dev", "prod"], default="dev")
    ap.add_argument("--max-gb", type=float, default=5.0)
    args = ap.parse_args()

    con = connect(args.target)
    name = con.execute("select current_database()").fetchone()[0]
    cur = con.execute("pragma database_size")
    cols = [d[0] for d in cur.description]
    sizes = {r[cols.index("database_name")]: r[cols.index("database_size")]
             for r in cur.fetchall()}
    if name not in sizes:
        sys.exit(f"{name} not in pragma database_size: {sorted(sizes)}")
    gb = to_bytes(sizes[name]) / 2**30
    print(f"{TARGETS[args.target]}: {sizes[name]} ({gb:.3f} GiB), limit {args.max_gb} GiB")
    if gb > args.max_gb:
        sys.exit(f"storage guard: {gb:.2f} GiB is over {args.max_gb} GiB")


if __name__ == "__main__":
    main()
