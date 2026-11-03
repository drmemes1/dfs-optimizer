# Rebuild plan

## Decisions so far
- **Stack:** Python serverless functions on Vercel (scipy `milp`/HiGHS optimizer) + a small web UI. $0/month.
- **Database:** Neon Postgres (free tier) via the Vercel integration — system of record. Schema in `db/migrations/`.
- **Daily inputs (uploaded):** DraftKings `DKSalaries.csv` (salaries, positions, draftable IDs) and the
  RotoGrinders premium projections export. DK's draftables API (`api.draftkings.com`) is blocked from
  Vercel, so we use DK's own CSV export instead of working around the block.
- **Fetched automatically:** DK lobby (slate ID, lock time, game list), RotoGrinders + RotoWire lineups
  (starting fives, Vegas lines, injury news), StatMuse (box scores for grading).
- **Not doing:** X/Twitter (API is paid; scraping breaks its terms), placing DK entries.
- **Learning:** parameters move only from measured bias, ~1% per slate, and only if a replay over all
  stored past slates does not get worse. Frozen keys (ownership knobs, max_from_team, pool rule) are
  never moved automatically.

## Roadmap
- [x] Core library: signals, pool rule, value board, MILP optimizer, DK scoring, name matching (tested)
- [x] Postgres schema + migrations; Neon connected; params v1 seeded
- [ ] Upload page + parsers: DKSalaries.csv, RotoGrinders export (FPTS=col 28, not 27), checked against DK lobby
- [ ] Lineups/Vegas/injuries from RotoGrinders + RotoWire; contingent flag; back-to-back flags
- [ ] Build job: ingest -> signals -> pool -> GPP/CASH lineups -> value board -> DB (re-runnable)
- [ ] **News & buzz panel:** latest RotoWire/RotoGrinders player news on the slate page, highlighting
      players in our lineups and on the value board
- [ ] Grade job: verified box scores (240/265/290 minutes + score cross-check), actuals, MAE ours vs RG,
      bucket/game bias, hindsight-perfect lineup, missed plays
- [ ] Learner: propose bias nudges, replay on past slates, append params with evidence; "What changed" page
- [ ] UI: slate page (lineups, value board A-D, injury watch, news & buzz), accuracy chart, DK CSV export,
      history browser
- [ ] Vercel Cron (build + grade) and a manual re-run button; preview review, then production

## Later / to be tested before it influences lineups
- **Buzz signal:** log per-player mention counts from Reddit r/dfsports (free for personal use) without
  affecting lineups; after a few weeks the learner checks whether buzz predicts beating/missing
  projection. Adopted only if the backtest proves it. A paid X API could feed the same slot.
