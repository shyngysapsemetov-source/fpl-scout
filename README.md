# fpl-scout

Trend analysis for Fantasy Premier League. Pick a player or a team and see their stats, where
they rank in the league, and rolling averages of the numbers that tend to come before points:
expected goals, expected assists, clean sheets, defensive contributions. Compare up to five
players side by side.

**Status: in progress.** The ingest job and the dbt project skeleton are working. Models and
the app are not built yet.

## How it works

```
FPL API ──► ingest/fetch.py ──► data/raw/dt=YYYY-MM-DD/ ──► ingest/load.py ──► raw schema ──► dbt ──► Streamlit app
            daily, GitHub Actions   JSON files, one per day     append per date    DuckDB      dev: local, prod: CI only
```

- **Ingest:** a light daily pull (players, teams, fixtures: two calls). Once a gameweek's data
  is checked, a full pull adds every player's per-fixture history (~800 calls). Each full pull
  is complete for the season. Price and ownership exist only as current values, so their
  history is the sequence of daily snapshots.
- **Load:** each day's JSON goes as-is into a `raw` schema, one snapshot per table per date. A
  rerun replaces that date.
- **Transform:** dbt Core on DuckDB. `dev` builds into a local file. `prod` lives on
  [MotherDuck](https://motherduck.com) (raw snapshots and models in one database) and is
  built only by CI from `main`.

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # .venv/bin on macOS/Linux
.venv/Scripts/python ingest/fetch.py            # light, or ~6 min full; --mode full|light, --limit 5
.venv/Scripts/python ingest/load.py             # today's folder into data/dev.duckdb; --all to backfill
cd dbt && ../.venv/Scripts/dbt build --profiles-dir .
```

## Data

From the public [Fantasy Premier League](https://fantasy.premierleague.com) API, which is
unofficial and has no published licence. The pipeline fetches politely (one request at a time,
with a pause between calls). The repo does not republish raw data; the app shows derived stats
only.

Past seasons (2023/24 to 2025/26), used for team ratings and model training, come from
[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League) (MIT).
They are loaded at build time and not republished.
