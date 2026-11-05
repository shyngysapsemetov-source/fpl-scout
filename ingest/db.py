"""Connection to the database holding the `raw` schema, per dbt target.

Keep in step with dbt/profiles.yml: raw and the models share one database per target (D19).
"""
import pathlib

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = {
    "dev": str(ROOT / "data" / "dev.duckdb"),
    # Switches to "md:fpl_scout" with profiles.yml once MOTHERDUCK_TOKEN exists (D14).
    "prod": str(ROOT / "data" / "prod.duckdb"),
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
    path = TARGETS[target]
    if not path.startswith("md:"):
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        if read_only and not pathlib.Path(path).exists():
            return None
    con = duckdb.connect(path, read_only=read_only)
    if not read_only:
        con.execute(DDL)
    return con
