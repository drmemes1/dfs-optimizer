# Automatic slate pipeline: unattended RotoGrinders reads and a pre-lock alert

- **Date:** 2026-11-05
- **Status:** design approved; awaiting implementation plan
- **Supersedes:** the `PLAN.md` line "uploads stay manual (RotoGrinders is behind a login; no
  stored credentials)" — for RotoGrinders only. DraftKings uploads stay manual by choice.

## Goal

By DraftKings' main-slate lock each night, GPP/CASH/Consensus lineups are built and waiting with
no action from the owner beyond one prompted CSV upload; and 15–20 minutes before lock his phone
receives the late-scratch picture so he can still change entries.

### Success criteria

1. At ~1:00pm ET the system identifies **today's main slate** on its own and builds lineups from
   automatically fetched RotoGrinders projections.
2. RotoGrinders is read with **no hand-pasted cookie**. A session is acquired by logging in and
   reused until rejected.
3. Shortly before lock — targeting 15–20 minutes out, and never after lock — a push notification
   lands naming flagged players in the owner's lineups and the same-slot swaps that fit. Severe
   scheduler delay can compress this; it cannot skip it.
4. No run can fail silently. Every job writes a `job_runs` row, and anything that breaks the 1pm
   build or the pre-lock sweep pushes a notification.
5. The owner still enters every lineup on DraftKings himself.

### Non-goals

- **Placing DraftKings entries.** Out of scope, as in `PLAN.md`.
- **Automating DraftKings salaries.** Decided against; see Decisions.
- Changing the optimizer, signal layer, pool rule, value board, or grading.
- Replacing the existing 20-minute `snapshot` job, which already refreshes pools and runs the
  late-scratch check and is left untouched.

## What exists today

- `dfs/sources/dk.py` fetches `www.draftkings.com/lobby/getcontests?sport=NBA` and picks a Classic
  (`gameTypeId 70`) draft group. Reachable and working.
- `dfs/sources/rg_fetch.py` reads the projections page using an `RG_COOKIE` env var pasted in by
  hand, which expires every week or two.
- `dfs/jobs.py` has `snapshot()` and `grade()`. Both write `job_runs` rows; `api/slate.py:39`
  returns the last three and `public/index.html:353` renders them.
- `api/cron.py` authenticates scheduled calls with `Authorization: Bearer $CRON_SECRET`.
- `.github/workflows/snapshots.yml` drives `snapshot` every 20 minutes, 1pm–midnight ET, because
  Vercel Hobby cron runs only once a day.
- `pipeline.check()` already computes flagged players and fitting same-slot swaps for the
  *entered* version of each lineup. The pre-lock alert consumes this; it does not reimplement it.

## Probe findings (2026-11-05)

1. **RotoGrinders has no bot protection on sign-in.** The page is served by plain
   `Apache/2.4.18 (Ubuntu)`; no Cloudflare headers, no challenge, no Turnstile. The form is
   `POST /sign-in` with inputs `username` and `password` and **no CSRF token**. A stdlib
   `urllib` request with a cookie jar is sufficient; no headless browser is required.
2. **The projections table is absent from the logged-out HTML** (2 tables, 9 `<tr>` total). Whether
   the logged-in page server-renders it or loads it by JavaScript is unresolved — see Open
   questions.
3. **`pick_main_slate()` is unsafe for unattended use.** It ranks Classic groups by prize pool with
   no date filter, and the lobby carries several days at once. Live data on 2026-11-05:

   | draft group | prize | games | start |
   |---|---|---|---|
   | 156184 | $1,033,857 | 7 | Nov 6, 7:00pm ET |
   | 156176 | $1,029,974 | 2 | Nov 5, 9:00pm ET |
   | 156191 | $475,586 | 2 | Nov 6, 10:00pm ET |
   | 156190 | $254,522 | 4 | Nov 6, 8:00pm ET |

   The top two differ by 0.4%, and the runner-up is a two-game 9pm slate. Prize pool alone cannot
   express "main draw", and today's date is not applied at all.
4. **`api.draftkings.com` draftables returns HTTP 403 to ordinary clients**, not only from Vercel.
   DraftKings blocks non-browser access to that endpoint generally.

## Design

### Daily schedule

| When | Job | Behavior |
|---|---|---|
| ~1:00pm ET | `build` | Identify today's main slate. If a stored DKSalaries upload exists for it, fetch RG projections and run `pipeline.run_build`, then push "lineups ready". If not, push "upload DKSalaries.csv" and stop without error. |
| 1pm–midnight, every 20 min | `snapshot` | Unchanged. |
| 6:00pm–8:30pm ET, every 5 min | `prelock` | Read the slate's lock time. Outside the alert condition, return immediately. Inside it, run a full RG + RotoWire sweep and push one alert. |

GitHub Actions remains a dumb clock: it calls `/api/cron?job=...` with `CRON_SECRET` exactly as the
snapshot workflow does. All credentials and all logic stay server-side. Actions minutes are free
because the repository is public.

### Alert trigger rule

Fire on the first tick where **`0 < minutes_to_lock <= 20`** and `alerts_sent` holds no `prelock`
row for the slate.

GitHub's scheduler is unreliable by several minutes, so consecutive ticks can land at T-22 and then
T-14, stepping over any 15–20 minute window. An upper bound with a fire-once marker guarantees an
alert no later than roughly 15 minutes before lock, typically nearer 20, and tolerates jitter. The
`0 <` lower bound suppresses alerts after lock, when they would be useless and misleading.

### Components

**`dfs/sources/dk.py`** — `pick_main_slate(lobby, date, override_group_id=None)`. Ranks Classic
groups by the number of games starting between 7:00pm and 8:00pm ET **on the target ET date**, with
prize pool as a tiebreak only. Returns nothing rather than guessing when no group qualifies, so a
dark night cannot be mistaken for a main slate. `override_group_id` forces a specific group and
bypasses ranking. Existing ET handling (`history.et_date`, the `ET` constant in
`dfs/sources/uploads.py`) is reused so daylight saving is handled in one place.

**`dfs/sources/rg_fetch.py`** — gains session acquisition:

- `login()` posts `username`/`password` from `RG_USER`/`RG_PASS` with a cookie jar and returns the
  session cookie.
- The cookie is persisted in Postgres and reused across invocations. On a response that redirects
  to `/sign-in` or returns 401/403, the cookie is discarded and `login()` runs **once** before the
  read is retried; a second failure raises `RGNotConnected`.
- `RG_COOKIE` remains supported and takes precedence, as a manual override when login breaks.

Persisting the cookie is load-bearing, not an optimization: without it every job run would log in,
producing dozens of logins a night. With it, logins occur roughly weekly.

**`dfs/notify.py`** — new and small. `push(title, body, priority="default")` POSTs to
`https://ntfy.sh/$NTFY_TOPIC`. Returns a status rather than raising, so a notifier outage cannot
fail a build that otherwise succeeded; the failure is recorded in the job's `errors`.

**`dfs/jobs.py`** — `build()` and `prelock()`, following the `snapshot()`/`grade()` shape: `_start`,
work, `_finish` with summary and errors. They therefore appear on the slate page with no UI work.

**`api/cron.py`** — accepts `job=build` and `job=prelock`.

**`db/migrations/008_rg_session_alerts.sql`**

- `rg_session(cookie text, acquired_at timestamptz)` — single row, server-side only.
- `alerts_sent(slate_id, kind, sent_at, unique(slate_id, kind))` — the unique constraint is what
  makes the alert fire exactly once across ~30 evening ticks.

### Notifications sent

| Event | Priority |
|---|---|
| Lineups built and ready | default |
| DKSalaries.csv not uploaded by 1pm | high |
| Pre-lock alert: flagged players and fitting swaps | high |
| Pre-lock sweep failed | high |
| 1pm build failed | high |

The pre-lock alert uses the entered version of each lineup, and falls back to the saved generated
lineups when none is marked entered, so a forgotten "Mark as entered" degrades the alert rather
than silencing it.

## Credentials and security

- `RG_USER`, `RG_PASS`, `NTFY_TOPIC` are Vercel environment variables marked sensitive, alongside
  the existing `RG_COOKIE` and `CRON_SECRET`. **No credentials go to GitHub**, whose only secrets
  stay `APP_URL`, `CRON_SECRET` and `VERCEL_BYPASS_SECRET`.
- The RotoGrinders password must be unique to RotoGrinders before it is stored.
- Credentials and session cookies are never logged, never returned by any endpoint, and never
  written to `job_runs` summaries or errors. A test asserts this.
- `NTFY_TOPIC` is an unguessable random string, since on ntfy.sh the topic name is the only secret.

## Risks

| Risk | Mitigation |
|---|---|
| RotoGrinders flags automated access and locks the account | Footprint kept minimal: roughly one login a week via the cached session, one projections read per build, and the pre-lock sweep. The dense 5-minute polling touches only the local database, never RotoGrinders. Automated access likely breaches RotoGrinders' terms; the owner accepted this risk knowingly for data he subscribes to, for personal use, with no redistribution. |
| Wrong slate built | The rewritten picker requires games in the 7:00–8:00pm ET window on the target date and returns nothing rather than guessing. Regression-tested against the near-tie above. |
| Credential compromise | Single store (Vercel, sensitive), unique password, never logged. |
| 1pm build silently does nothing | Missing upload and any failure both push a high-priority notification with hours of runway before lock. |
| Pre-lock alert never arrives | `<= 20` bound tolerates scheduler jitter; sweep failures push the error rather than staying quiet. |

## Testing

Test-driven, with network access behind the `fetch=` injection seam `jobs.snapshot()` already uses.

1. **Slate picker** against a fixture of the real 2026-11-05 lobby: must choose the 7-game 7:00pm
   group and must not choose the 2-game 9:00pm group despite the 0.4% prize gap; must respect the
   target date; must return nothing when no group starts in the window; must honor the override.
2. **Alert timing** as a pure function of (now, lock time, already-sent): fires in window; fires on
   a jittered tick that skips 15–20 and lands at T-14; fires only once across many ticks; does not
   fire after lock; does not fire outside the window.
3. **Session reuse** with a fake transport: a valid cookie causes no login; a rejected cookie causes
   exactly one re-login and one retry; a second rejection raises `RGNotConnected`.
4. **Notifier**: payload shape, and that a notifier outage does not fail an otherwise good build.
5. **Secret hygiene**: no credential or cookie appears in any `job_runs` row or log line.

## Open questions

1. **Is the logged-in projections table in the HTML, or loaded by JavaScript?** Unresolved pending a
   login probe. If HTML, `rg_fetch.html_to_table_text()` is reused unchanged. If JavaScript, the
   underlying JSON endpoint must be identified and a parser written for it; `rg_table.parse()`'s
   column contract still applies. This changes implementation effort, not architecture, because
   `html_to_table_text()` already fails loudly when the table is missing.

## Decisions

| Decision | Reasoning |
|---|---|
| DraftKings salaries stay a manual upload (option C) | The draftables API blocks ordinary clients, and defeating that protection was rejected. Automating the owner's DraftKings login was rejected as disproportionate for a real-money account. Taking salaries from RotoGrinders would cost DraftKings' authoritative positions and multi-position eligibility, which the MILP depends on. The upload is one prompted tap and preserves correctness. |
| GitHub Actions as clock, logic on Vercel | RotoGrinders login needs no browser, so there is no reason to move credentials or logic into CI. One credential store, and the frequent pre-lock polling is cheap HTTP. |
| Session cookie cached in Postgres | Keeps logins to roughly weekly instead of dozens nightly — directly serves the account-safety risk above. |
| ntfy.sh for push | Free, no account, one HTTP call, and preserves the project's $0/month constraint. |
| Alert bound at `<= 20` minutes rather than a 15–20 band | GitHub's scheduler jitter would otherwise skip the band entirely. |
