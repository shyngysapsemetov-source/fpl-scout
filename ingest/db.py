"""Connection to the database holding the `raw` schema, per dbt target.

Keep in step with dbt/profiles.yml: raw and the models share one database per target (D19).
"""
import os
import pathlib
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = {
    "dev": str(ROOT / "data" / "dev.duckdb"),
    # MotherDuck (D14, D22). Needs MOTHERDUCK_TOKEN in the environment, which only CI has.
    "prod": "md:fpl_scout",
}

DDL = """
create schema if not exists raw;
create table if not exists raw.bootstrap_static (
    dt date not null, fetched_at timestamptz not null, payload json not null);
create table if not exists raw.fixtures (
    dt date not null, fetched_at timestamptz not null, payload json not null);
create table if not exists raw.element_summaries (
    dt date not null, fetched_at timestamptz not null, element_id integer not null,
    history json not null);
"""


def connect(target, read_only=False):
    """read_only returns None when a local file doesn't exist yet. MotherDuck ignores it, and
    the DDL runs, which is harmless because every statement is `if not exists`."""
    path = TARGETS[target]
    if path.startswith("md:"):
        if not os.environ.get("MOTHERDUCK_TOKEN"):
            sys.exit("MOTHERDUCK_TOKEN is not set; prod is CI-only")
        con = duckdb.connect(path)
        con.execute(DDL)
        return con
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    if read_only and not pathlib.Path(path).exists():
        return None
    con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        con.execute(DDL)
    return con
