# NBA DFS Optimizer (DraftKings Classic)

Nightly pipeline: identify the DK slate, ingest salaries/status (DK), projections
(RotoGrinders CSV upload), Vegas lines and starting lineups; apply the tuned signal
layer; build GPP and CASH lineups with a MILP (scipy/HiGHS); grade against verified
box scores the next morning; learn parameters only from measured bias.

## Layout
- `dfs/` — core library (no I/O): names, DK scoring, params, signal layer, pool rule,
  value board, optimizer.
- `db/migrations/` — Postgres schema (system of record).
- `tests/` — `python -m pytest`.

## Local development
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
