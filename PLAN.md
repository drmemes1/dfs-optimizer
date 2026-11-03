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
- **Learning:** every graded night is kept forever and the learner uses **all** of them each morning
  (recency-weighted, never discarded). Parameters move only from bias that holds across enough nights,
  ~1% per slate, and only if a replay over all stored past slates does not get worse. Frozen keys (ownership knobs, max_from_team, pool rule) are
  never moved automatically.

## Roadmap (priority order from the addendum)
- [x] Core library: signals, pool rule, value board, MILP optimizer, DK scoring, shared name matching (#11)
- [x] Postgres schema + migrations; Neon connected; params v1 seeded
- [x] Upload page + parsers (DKSalaries.csv; RotoGrinders by header name, never the PrizePicks column)
- [x] Build job: signals -> pool -> GPP/CASH/Consensus/Max-projection lineups -> value board -> DB
- [x] #1 Late-scratch checker: per-game lock time from DKSalaries.csv Game Info, countdown, "Re-check
      inactives" (fresh RG upload) flags Out/Doubtful/Questionable/no-projection players in saved and
      entered lineups with same-slot swaps that fit the salary left and haven't tipped
- [x] #2 Lock / Exclude per player + Rebuild, showing the projection cost vs the unconstrained build
- [x] #3 Constraint controls on the form (max_from_team, max_from_game, max_own_sum, min_salary,
      min_uniques, ceil_weight, own_penalty), defaulted from params, not written back to params
- [x] #4 Exact uploaded bytes archived per slate (bytea + sha256)
- [x] #5 Mark as entered; validated swaps keep the generated version (players_original)
- [x] #6 (part) Full pool snapshot per build/refresh; each signal multiplier in its own column
- [x] #7 (part) Every lineup records params_version; params rows carry kind + per-key changes
- [x] #15 Phone layout (no sideways scroll; lineup cards and value board usable at 390px)
- [x] Consensus lineup: most-picked players across the GPP set, all rules applied
- [ ] Starting lineups (RotoGrinders + RotoWire cross-check -> starter_consensus, contingent), Vegas lines
      (#10: pace_exponent 0.5, totals never a stacking reason, per-game bias stored), back-to-back flags.
      Priority: consensus starter was the strongest measured predictor (73% vs 20% on cheap plays).
- [ ] #6 Grading job: box scores accepted only if each team's minutes == 240/265/290 AND summed points ==
      the final score from an independent source; grade the *entered* version of each lineup
- [ ] #12 Hindsight-optimal lineup per slate (ceiling, salary used, structure vs projection error)
- [ ] #7 Learner: nudges only from measured bias, <=1% per key per slate, minimum sample enforced in code,
      replay over all stored slates must not get worse, append params with old -> new and why
- [ ] #8 Recent-form check: implied DK pts/min vs last-5/last-10 actual rate, flag >15% required increase
      (needs a reachable game-log source; cdn.nba.com is blocked from Vercel)
- [ ] News & buzz panel; backfill + Research page; accuracy-over-time chart
- [ ] #13 Automation: uploads stay manual (RotoGrinders is behind a login; no stored credentials). Cron
      only for jobs whose sources are reachable (grading), and every job reports failures visibly.
- [ ] #14 If the RG projections *page* is ever parsed: split each <tr> innerText on tabs (not
      querySelectorAll('td')); FPTS=28, FPTS/$=29, pOWN=30, FLOOR=33, CEIL=34, SIM85=44; "-" = no tag.
- Production deploy can wait; preview + the real database is the working setup for now.

## Binding rules
- #9 No value-play filters. Pool rule stays: salary >= 5500 OR consensus starter OR 24+ minutes.
- Nudges from measured bias only; no hand-tuned changes without a graded result. Data-integrity guards
  (starter consensus, name matching, box-score verification) are always on.

## Later / to be tested before it influences lineups
- **Buzz signal:** log per-player mention counts from Reddit r/dfsports (free for personal use) without
  affecting lineups; after a few weeks the learner checks whether buzz predicts beating/missing
  projection. Adopted only if the backtest proves it. A paid X API could feed the same slot.
