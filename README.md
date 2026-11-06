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

## Configuration (Vercel environment variables, all sensitive)
- `RG_USER`, `RG_PASS` — RotoGrinders login. Use a password unique to RotoGrinders.
- `NTFY_TOPIC` — an unguessable random string; on ntfy.sh the topic name is the only secret.
  Subscribe to it in the ntfy phone app. Optional `NTFY_SERVER` for a self-hosted instance.
- `RG_COOKIE` — optional manual override if login breaks; takes precedence over the stored session.
- `CRON_SECRET` — must equal the GitHub Actions repository secret of the same name.

GitHub Actions repository secrets: `APP_URL`, `CRON_SECRET`, `VERCEL_BYPASS_SECRET`.
No RotoGrinders credentials are stored in GitHub.

## Scheduled jobs
| Job | When | What |
|---|---|---|
| `build` | ~1pm ET | Pick today's main slate, build from the stored DKSalaries upload plus freshly read RotoGrinders projections. Pushes a nudge if the upload is missing. |
| `snapshot` | every 20 min, 1pm–midnight ET | Record lineup/injury/Vegas changes; refresh the pool and run the late-scratch check. |
| `prelock` | every 5 min, evening | Does nothing until a slate is within 20 minutes of lock, then sweeps and pushes the flagged players with fitting swaps. |
| `grade` | 14:30 UTC | Grade yesterday's slates against verified box scores. |

Local development needs Python 3.10+ (the code uses `X | None` annotations). The DB-backed tests
run only when `TEST_DATABASE_URL` is set and skip otherwise:
```bash
TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q
```
