# Recent form: pull back projections that require an implausible jump

- **Date:** 2026-11-06
- **Status:** implemented; exclusions corrected 2026-11-08 after code review
- **Supersedes:** `PLAN.md` #8, which parked this as "needs a reachable game-log source;
  cdn.nba.com is blocked from Vercel". That note sent us to the wrong door: box scores already
  come from ESPN's public API (`site.api.espn.com`), which returns per-player minutes and counting
  stats and is reachable from Vercel today. Basketball-Reference is only the verification source.

## Goal

When RotoGrinders projects a player at a rate his recent play does not support, pull the
projection partway back toward that rate — and show the gap so the owner can overrule it.

### Why this and not "boost hot players"

Every signal in this system is a **correction applied to a professional projection**. RotoGrinders
already weights recent performance heavily, so a hot-streak multiplier mostly re-applies something
already priced in, and risks double-counting.

The gap between a projection and the player's recent per-minute rate is different: it is
information the projection cannot contain, because it *is* the projection being tested. A
projection far above recent production is a bet on a bounce or a role change. Sometimes that bet
is right. Measuring how far out on a limb it sits is new information either way.

### Success criteria

1. Every pool player with enough history gets a `m_form` multiplier derived from his last N games'
   DK points per minute, and it is stored per build so it can be graded.
2. A projection needing more than `form_tolerance` above the recent rate is scaled back, never
   below `form_max_cut`. The adjustment is symmetric and capped in both directions.
3. A player with too little history, too few minutes, or a changing role gets `m_form = 1.0` —
   no data, no opinion.
4. The value board and player pool show the required jump, so the owner can disagree with it.
5. Game logs are verified by the same box-score rules grading uses; an unverified game is never
   written.

### Non-goals

- Defence-vs-position, usage redistribution, implied team totals. Separate work.
- Changing the pool rule. `PLAN.md` #9 is binding: salary >= 5500 OR consensus starter OR 24+
  minutes. Recent form scales a projection; it never removes a player from the pool.
- Predicting minutes. Minutes come from RotoGrinders as they do today.

## Decision on record

The owner decided on 2026-11-06 that new signals **affect lineups immediately** rather than being
shadow-logged until graded. This reverses `PLAN.md`'s "nudges from measured bias only; no
hand-tuned changes without a graded result", which was put to him explicitly with that cost
stated. `PLAN.md` is updated by this work so the rule and the code agree.

The practical consequence: `m_form`'s starting constants are **judgement, not measurement**, and
there are zero graded slates to check them against. They are therefore conservative by design
(see Safety), stored per player per build, and are the first thing the learner should revisit.

## Design

### Data: `player_games`

```sql
CREATE TABLE player_games (
  player_id  bigint NOT NULL REFERENCES players(id),
  game_date  date   NOT NULL,
  team       text, opp text,
  min        numeric NOT NULL,
  dk_pts     numeric NOT NULL,
  pts int, fg3m int, reb int, ast int, stl int, blk int, tov int,
  dnp        boolean NOT NULL DEFAULT false,
  source     text,
  PRIMARY KEY (player_id, game_date)
);
```

One row per player per night. `dk_pts` is computed by the existing `dfs/scoring.py` from the same
counting stats the grader uses, so a game log and a graded slate can never disagree about what a
performance was worth.

### Ingest

`dfs/gamelogs.py`, new and small:

- `ingest_day(conn, game_date, day=None) -> dict` — takes `boxscores.fetch_day(game_date)` output,
  writes one `player_games` row per verified player line, upserts on `(player_id, game_date)`.
  **Unverified games are skipped**, matching the grader: `PLAN.md`'s data-integrity guard applies
  here too, since a wrong box score would poison the rate for ten days.
- `backfill(conn, through_date, days) -> dict` — walks back `days` nights, calling `ingest_day` for
  each, skipping dates already complete. Used once to seed history and harmless to re-run.
- `recent_rates(conn, player_ids, before_date, lookback) -> {player_id: {rate, minutes, games}}` —
  DK points per minute over that player's last `lookback` games with `min > 0`, strictly before
  the slate date.

Players are matched to `players.id` through the existing `NameIndex`/`name_norm`, the same path
grading uses. A box-score name that matches nobody is reported, never guessed at.

### Signal

`dfs/signals.py` gains `m_form` as a seventh component, with `"m_form"` added to
`SIGNAL_COLUMNS` so it is stored per build and graded like the others.

```
implied_rate  = proj_rg / projected_minutes
required_jump = implied_rate / recent_rate - 1

if required_jump >  form_tolerance:  m_form = max(form_max_cut,   (1 + form_tolerance) * recent_rate / implied_rate)
if required_jump < -form_tolerance:  m_form = min(form_max_boost, (1 - form_tolerance) * recent_rate / implied_rate)
otherwise                            m_form = 1.0
```

The first branch caps the implied rate at `form_tolerance` above what the player has actually been
producing, rather than scaling by the raw gap — so a wild projection is pulled to the edge of
plausible, not to the recent rate itself.

### Parameters (`dfs/params.py`, signals block)

| Key | Start | Why |
|---|---|---|
| `form_lookback_games` | 10 | `PLAN.md` #8's "last-5/last-10"; 10 is the steadier of the two |
| `form_tolerance` | 0.15 | `PLAN.md` #8's ">15% required increase" |
| `form_max_cut` | 0.85 | A projection can lose at most 15%. One signal should not halve a player. |
| `form_max_boost` | 1.08 | Deliberately smaller than the cut: RotoGrinders already chases hot play, so the upside is the side more likely to double-count |
| `form_min_games` | 4 | Below this the rate is noise |
| `form_min_minutes_per_game` | 15 | **Corrected 2026-11-08:** this was a 60-minute *lookback total*, which is six minutes a night - ten 8-minute blowout appearances cleared it, which is the exact garbage-time rate the gate exists to stop. A per-game floor does stop it and does not change behaviour for rotation players. |

All six are new `signals` keys and therefore movable by the learner later. None are frozen.

### Safety: when the signal says nothing

`m_form = 1.0` — never an adjustment — when any of these hold:

- Fewer than `form_min_games` games, or fewer than `form_min_minutes` total minutes.
- No projected minutes, or a recent rate of zero.
- **The player is flagged `contingent` or `role_change`.** His role is changing, so his recent
  rate describes a job he no longer has. This is the single most important exclusion in the design.

  **Corrected 2026-11-08 after review.** The first implementation used `contingent` alone, which
  `lineups.py` sets only for players **under $5,500 whose teammate's status is unresolved**
  (Q/GTD/D). The ordinary role change - a starter ruled *out* and his backup promoted - is
  resolved, not unresolved, and is frequently above $5,500, so it set `contingent = False` and the
  signal cut the promoted backup at full strength on precisely the night his raised projection was
  most likely correct. A worked case: a $6,200 backup at 0.75 DK/min over 10 games, re-projected
  at 34 points in 32 minutes, was cut to 0.85 - and to roughly 0.80 once a back-to-back and a
  blowout line compounded. `role_change` is now computed separately in `apply_lineups`: true when
  any team-mate sharing a position group is *absent* (D/O/OUT/NOPROJ) at any salary. Questionable
  team-mates are excluded on purpose, because a questionable player usually plays.
- **The lineup pages did not load (`role_known` is false).** Both role flags come from those
  pages, so when neither loads every player looks role-stable and the exclusion would silently
  disappear - on exactly the nights the system already warns that it is flying blind.
- **The player's last game was more than 14 days before the slate.** A returning injury makes the
  recent rate describe a different player.

### Visibility

The pool table and value board gain a "form" column showing the required jump as a percentage,
coloured only when the signal actually fired. The build report names how many players were scaled
and how many were skipped for want of history, so a day when the game logs failed to load is
obvious rather than silent.

### Scheduling

`jobs.grade()` already runs nightly and already fetches verified box scores. It calls
`gamelogs.ingest_day` for the same day in the same pass, so the log stays current with no new
schedule. The initial backfill is a one-off `job=backfill` run.

## Risks

| Risk | Mitigation |
|---|---|
| Double-counting what RotoGrinders already priced in | Asymmetric caps: the boost side, which is the side RG already chases, is capped tighter than the cut side. `m_form` is stored per player per build so grading measures it directly. |
| Punishing a player who just got a bigger role | `contingent` players are excluded outright; the 14-day staleness rule covers returns from injury. |
| A bad box score poisoning a rate for ten days | Only verified games are written, the same rule grading uses. |
| Constants are judgement, not measurement | Conservative caps, and they are the learner's first target. The owner accepted this explicitly. |
| Backfill hammering ESPN | ~5–12 summary calls per night over 14 nights, once. Nightly upkeep is the grader's existing fetch. |

## Testing

- `recent_rates` arithmetic against hand-built logs, including a player with a zero-minute game and
  one whose games straddle the lookback edge.
- Each exclusion returns exactly 1.0: too few games, too few minutes, no projected minutes,
  contingent, stale last game.
- The cut is capped at `form_max_cut` for an absurd projection, and the boost at `form_max_boost`.
- A projection inside tolerance is untouched — the common case must be a no-op.
- `ingest_day` skips an unverified game and reports it.
- `backfill` is idempotent: running it twice writes the same rows.
- A full build with game logs present produces different lineups than one without, proving the
  signal reaches the optimizer.

## Open questions

1. How many days of backfill before the signal is trustworthy? 14 gives most players 4–8 games.
   The `form_min_games` floor makes a short history self-limiting rather than wrong, so this is a
   coverage question, not a correctness one.
