# fpl-scout: project instructions

Public repo. A trend tool for Fantasy Premier League. Pick a player or team and see their stats,
their rank in the league, and rolling averages of the metrics that predict returns. Compare up to
5 players.

## How we work: pairing

He makes the design calls: grain, windows, metric definitions, tests, what ships. The assistant
drafts code to his decisions. **When a model needs a judgment call, stop and put it to him with
a recommendation. Don't decide it in code.** Theory comes in short pieces, attached to the next
build step. `LEDGER.md` (local, gitignored) records his decisions and assistant errors; append
to it as they happen ([ATTR-3]).

## Commands (from the repo root, Git Bash)

- Fetch: `.venv/Scripts/python ingest/fetch.py` (`--limit 5` for a quick test)
- Build: `cd dbt && ../.venv/Scripts/dbt build --profiles-dir .`
- The agent loop: run `dbt build` and fix failures. **Stop after 3 failed attempts on the same
  error** and report. Never weaken or delete a test to make it pass.

## Rules

- `--target prod` is CI-only. Never build prod locally.
- Raw API data is never committed or republished. The API is unofficial and unlicensed.
- Model grain is player × fixture, not player × gameweek. Double and blank gameweeks exist.
- Public repo: no personal details, nothing from the career repos ([PRIV-1]).
