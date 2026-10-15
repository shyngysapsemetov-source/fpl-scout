# fpl-scout

Trend analysis for Fantasy Premier League. Pick a player or a team and see their stats, where
they rank in the league, and rolling averages of the numbers that tend to come before points:
expected goals, expected assists, clean sheets, defensive contributions. Compare up to five
players side by side.

**Status: in progress.** The ingest job and the dbt project skeleton are working. Models and
the app are not built yet.

## How it works

```
FPL API ──► ingest/fetch.py ──► data/raw/dt=YYYY-MM-DD/ ──► dbt (DuckDB) ──► Streamlit app
            daily, GitHub Actions   raw JSON, one folder per day       dev: local, prod: CI only
```

- **Ingest:** a daily pull of every player, team, fixture and per-fixture history. The
  per-fixture stats are complete for the season on every run. Price and ownership exist only as
  current values, so their history is the sequence of daily folders.
- **Transform:** dbt Core on DuckDB. `dev` builds locally. `prod` is built only by CI from
  `main`.

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # .venv/bin on macOS/Linux
.venv/Scripts/python ingest/fetch.py            # ~6 min for all players; --limit 5 to test
cd dbt && ../.venv/Scripts/dbt build --profiles-dir .
```

## Data

From the public [Fantasy Premier League](https://fantasy.premierleague.com) API, which is
unofficial and has no published licence. The pipeline fetches politely (one request at a time,
with a pause between calls). The repo does not republish raw data; the app shows derived stats
only.
