# Automatic Slate Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build lineups unattended from an automatic RotoGrinders login, and push a phone alert 15–20 minutes before DraftKings' main-slate lock carrying flagged players and fitting swaps.

**Architecture:** GitHub Actions stays a dumb clock that calls `/api/cron?job=...` with `CRON_SECRET`; all credentials and logic stay server-side on Vercel. A new `dfs/notify.py` pushes to ntfy. A new `dfs/prelock.py` holds the pure timing and slate-overlap decisions. `dfs/sources/rg_fetch.py` gains a login that caches its session cookie in Postgres so logins stay roughly weekly. `dfs/jobs.py` gains `prelock()` and `build()` alongside the existing `snapshot()`/`grade()`.

**Tech Stack:** Python 3.12, stdlib `urllib` + `http.cookiejar` (no new runtime dependencies), psycopg 3, Postgres (Neon), pytest, GitHub Actions, ntfy.sh.

**Spec:** `docs/superpowers/specs/2026-11-05-automatic-slate-pipeline-design.md`

## Global Constraints

- **No new runtime dependencies.** `requirements.txt` stays `numpy`, `scipy`, `psycopg[binary]`, `beautifulsoup4`. The login uses stdlib `urllib` + `http.cookiejar`; the probe proved no browser is needed.
- **Credentials live only in Vercel env vars** (`RG_USER`, `RG_PASS`, `NTFY_TOPIC`), marked sensitive. GitHub holds only `APP_URL`, `CRON_SECRET`, `VERCEL_BYPASS_SECRET`.
- **Credentials and session cookies are never logged, never returned by any endpoint, and never written to a `job_runs` summary or error.**
- **Test fixtures must be synthetic.** The repo is public; committing captured RotoGrinders projections would redistribute subscriber-only data. Hand-write table fixtures with invented players. The DraftKings lobby response is public and may be committed verbatim.
- **Every job writes a `job_runs` row** via the existing `_start`/`_finish` helpers so a broken scheduler stays visible on the slate page.
- **Pool rule, signal layer, optimizer, value board and grading are not touched.** `PLAN.md` binding rule #9 stands: no value-play filters; pool rule stays `salary >= 5500 OR consensus starter OR 24+ minutes`.
- **Frozen params are never written by this work.** Nothing here calls `paramstore.apply_changes`.
- **RotoGrinders footprint stays minimal:** one cached session reused until rejected, one projections read per build and per pre-lock sweep. The 5-minute polling must touch only Postgres.

## Review Focus

These are the failure modes the spec implies that would otherwise reach production untested. Each has a test assigned to the task owning the code.

1. **RotoGrinders returns a different slate's players than the DraftKings file** — the page is slate-scoped (spec open question 1), and a silent wrong-slate build is the most dangerous outcome in this design. Expected: refuse to build, record the mismatch, push an alert. *Test in Task 3 (`overlap_ratio`) and Task 6 (build refuses).*
2. **The RotoGrinders password is wrong or revoked during the evening window** — the job runs every 5 minutes, so a naive retry means ~36 failed logins a night, which is exactly how an account gets locked. Expected: at most 3 login attempts per 30 minutes, then fail fast without contacting RotoGrinders. *Test in Task 3 (backoff).*
3. **`slates.lock_time_utc` is NULL** — the column is nullable and DraftKings' Game Info can be unparseable. Expected: no alert, no crash, recorded as a skip. *Test in Task 2 (`should_alert`).*
4. **Daylight saving shifts the ET evening window** — the GitHub cron is UTC, so a fixed hour range drifts by one hour twice a year. Expected: correctness comes from the lock time in the database, not from the cron hours; the schedule polls wide enough to cover both offsets. *Test in Task 2 (`should_alert` is purely lock-time driven, verified across an EST and an EDT date).*
5. **`NTFY_TOPIC` is unset or ntfy is down** — alerts would vanish with the job still reporting success. Expected: `push()` returns a failure instead of raising, the job still completes its real work, and the notifier failure appears in the run's `errors`. *Test in Task 1 (no topic, transport raises) and Task 4 (sweep succeeds, error recorded).*

---

### Task 0: Working Python environment

Nothing can be test-driven until the test suite runs. System Python is 3.9.6, which cannot import this codebase (`X | None` annotations raise `TypeError` at import), and `psycopg` is absent. Homebrew 7.0.6 is installed on arm64.

**Files:**
- Create: `.gitignore` entry for `.venv` (verify it is already ignored)

- [ ] **Step 1: Install Python 3.12 and Postgres**

```bash
brew install python@3.12 postgresql@16
brew services start postgresql@16
```

- [ ] **Step 2: Create the virtualenv and install dependencies**

```bash
cd /Users/igorkhabensky/dfs-optimizer-1
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt
.venv/bin/python -V
```

Expected: `Python 3.12.x`

- [ ] **Step 3: Confirm the existing suite passes before changing anything**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass or skip. If anything fails, STOP and report — this plan assumes a green baseline.

- [ ] **Step 4: Confirm `.venv` is git-ignored**

```bash
grep -q '^\.venv' .gitignore || echo '.venv/' >> .gitignore
git status --porcelain
```

Expected: `.venv/` does not appear as untracked.

- [ ] **Step 5: Create the test database and verify the DB-backed tests run**

```bash
/opt/homebrew/opt/postgresql@16/bin/createdb dfs_test
TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q tests/test_db.py
```

Expected: PASS (not "skipped"). If skipped, `TEST_DATABASE_URL` is not reaching pytest.

- [ ] **Step 6: Commit if `.gitignore` changed**

```bash
git add .gitignore && git commit -m "chore: ignore .venv" || echo "nothing to commit"
```

---

### Task 1: ntfy notifier

**Files:**
- Create: `dfs/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `notify.push(title: str, body: str, priority: str = "default", tags: str = "", post=None) -> dict` returning `{"ok": bool, "error": str | None}`. Never raises. `post` is an injection seam with signature `post(url: str, data: bytes, headers: dict) -> None`.

**Design note:** HTTP headers must be ASCII, so `title` and `tags` are ASCII-only constants chosen by callers. All player names go in `body`, which is sent as UTF-8 bytes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notify.py
import pytest

from dfs import notify


def test_no_topic_configured_returns_failure_without_calling_out(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    calls = []
    out = notify.push("Title", "body", post=lambda *a, **k: calls.append(a))
    assert out["ok"] is False
    assert "NTFY_TOPIC" in out["error"]
    assert calls == []


def test_posts_to_the_topic_with_title_and_priority(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
    seen = {}

    def fake_post(url, data, headers):
        seen.update(url=url, data=data, headers=headers)

    out = notify.push("Lineups ready", "4 flags", priority="high", tags="warning", post=fake_post)
    assert out["ok"] is True
    assert seen["url"] == "https://ntfy.sh/secret-topic"
    assert seen["headers"]["Title"] == "Lineups ready"
    assert seen["headers"]["Priority"] == "high"
    assert seen["headers"]["Tags"] == "warning"
    assert seen["data"] == b"4 flags"


def test_non_ascii_body_is_utf8_encoded(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    seen = {}
    notify.push("Alert", "Nikola Jokic OUT", post=lambda u, d, h: seen.update(data=d))
    assert seen["data"] == "Nikola Jokic OUT".encode("utf-8")


def test_transport_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(url, data, headers):
        raise OSError("connection refused")

    out = notify.push("T", "b", post=boom)
    assert out["ok"] is False
    assert "connection refused" in out["error"]


def test_custom_server_is_honored(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.com/")
    seen = {}
    notify.push("T", "b", post=lambda u, d, h: seen.update(url=u))
    assert seen["url"] == "https://ntfy.example.com/t"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dfs.notify'`

- [ ] **Step 3: Write the implementation**

```python
# dfs/notify.py
"""Phone push via ntfy. The topic name is the only secret, so it lives in NTFY_TOPIC
(sensitive, Vercel) and is never logged or echoed back by any endpoint.

push() never raises: a notifier outage must not fail a build that otherwise succeeded. The
caller records the returned error on the job run instead."""
import os
import urllib.request

DEFAULT_SERVER = "https://ntfy.sh"


def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15):
        pass


def configured() -> bool:
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def push(title: str, body: str, priority: str = "default", tags: str = "", post=None) -> dict:
    """Returns {"ok": bool, "error": str | None}. title/tags must be ASCII (HTTP headers);
    body carries anything, sent as UTF-8."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return {"ok": False, "error": "NTFY_TOPIC is not set in Vercel; push alerts are disabled"}
    server = os.environ.get("NTFY_SERVER", DEFAULT_SERVER).strip().rstrip("/") or DEFAULT_SERVER
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        (post or _post)(f"{server}/{topic}", body.encode("utf-8"), headers)
        return {"ok": True, "error": None}
    except Exception as e:  # noqa: BLE001 - a failed push must never fail the job
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_notify.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add dfs/notify.py tests/test_notify.py
git commit -m "Add ntfy push notifier

push() never raises, so a notifier outage cannot fail a build that
otherwise succeeded; the caller records the error on the job run."
```

---

### Task 2: Pre-lock timing decision and schema

The alert must fire once, no later than roughly 15 minutes before lock, despite GitHub's scheduler jitter. The decision is a pure function of (lock time, now, already-sent) so it is testable without a clock or a network.

**Files:**
- Create: `dfs/prelock.py`
- Create: `db/migrations/008_rg_session_alerts.sql`
- Test: `tests/test_prelock.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `prelock.ALERT_LEAD_MINUTES = 20`
  - `prelock.minutes_to_lock(lock_time_utc, now) -> float | None`
  - `prelock.should_alert(lock_time_utc, now, already_sent: bool, lead_minutes: int = ALERT_LEAD_MINUTES) -> bool`
  - `prelock.overlap_ratio(rg_names, dk_names) -> float`
  - `prelock.format_alert(slate_date, flags: list[dict], lineups_checked: int) -> tuple[str, str]` returning `(title, body)`; title is ASCII.
  - Tables `rg_session` and `alerts_sent`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prelock.py
from datetime import datetime, timedelta, timezone

from dfs import prelock

LOCK = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)  # Nov 5, 7:00pm ET (EST)


def at(minutes_before):
    return LOCK - timedelta(minutes=minutes_before)


def test_minutes_to_lock_is_none_when_lock_time_missing():
    assert prelock.minutes_to_lock(None, at(30)) is None


def test_minutes_to_lock_counts_down():
    assert prelock.minutes_to_lock(LOCK, at(18)) == 18.0


def test_minutes_to_lock_is_negative_after_lock():
    assert prelock.minutes_to_lock(LOCK, LOCK + timedelta(minutes=5)) == -5.0


def test_does_not_fire_too_early():
    assert prelock.should_alert(LOCK, at(45), already_sent=False) is False


def test_fires_inside_the_lead_window():
    assert prelock.should_alert(LOCK, at(18), already_sent=False) is True


def test_fires_on_a_jittered_tick_that_skipped_the_15_to_20_band():
    # GitHub delayed the run: ticks landed at T-22 then T-14. The T-14 tick must still fire.
    assert prelock.should_alert(LOCK, at(22), already_sent=False) is False
    assert prelock.should_alert(LOCK, at(14), already_sent=False) is True


def test_fires_only_once():
    assert prelock.should_alert(LOCK, at(18), already_sent=True) is False


def test_does_not_fire_after_lock():
    assert prelock.should_alert(LOCK, LOCK, already_sent=False) is False
    assert prelock.should_alert(LOCK, LOCK + timedelta(minutes=1), already_sent=False) is False


def test_null_lock_time_never_fires():
    assert prelock.should_alert(None, at(18), already_sent=False) is False


def test_decision_is_lock_time_driven_so_dst_cannot_drift_it():
    # Same 7:00pm ET lock on an EDT date is 23:00Z, not 00:00Z. The function is told the real
    # instant, so the same relative moment fires in both offsets.
    edt_lock = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
    assert prelock.should_alert(edt_lock, edt_lock - timedelta(minutes=18), already_sent=False) is True
    assert prelock.should_alert(edt_lock, edt_lock - timedelta(minutes=45), already_sent=False) is False


def test_accepts_a_naive_datetime_as_utc():
    naive = LOCK.replace(tzinfo=None)
    assert prelock.should_alert(naive, at(18), already_sent=False) is True


def test_overlap_ratio_matches_on_normalized_names():
    dk = ["Devin Booker", "Jalen Green", "Dillon Brooks", "Alperen Sengun"]
    rg = ["devin booker", "Jalen  Green", "D. Brooks"]
    r = prelock.overlap_ratio(rg, dk)
    assert 0.4 <= r <= 0.6  # Booker + Green match; "D. Brooks" and Sengun do not


def test_overlap_ratio_is_one_for_identical_lists():
    names = ["Devin Booker", "Jalen Green"]
    assert prelock.overlap_ratio(names, names) == 1.0


def test_overlap_ratio_is_zero_for_a_different_slate():
    assert prelock.overlap_ratio(["Nikola Jokic"], ["Devin Booker", "Jalen Green"]) == 0.0


def test_overlap_ratio_of_empty_dk_pool_is_zero():
    assert prelock.overlap_ratio(["Devin Booker"], []) == 0.0


def test_format_alert_title_is_ascii_and_body_names_the_players():
    flags = [
        {"name": "Nikola Jokic", "slot": "C", "status": "OUT", "locked": False, "mode": "GPP",
         "rank": 1, "entered": True, "salary_room": 11200,
         "suggestions": [{"name": "Alperen Sengun", "salary": 8700, "proj": 44.1, "proj_delta": -9.2}]},
    ]
    title, body = prelock.format_alert("2026-11-05", flags, lineups_checked=13)
    title.encode("ascii")  # must not raise
    assert "Nikola Jokic" in body
    assert "OUT" in body
    assert "Alperen Sengun" in body


def test_format_alert_says_so_when_nothing_is_flagged():
    title, body = prelock.format_alert("2026-11-05", [], lineups_checked=13)
    title.encode("ascii")
    assert "13" in body
    assert "no" in body.lower()


def test_format_alert_marks_a_locked_player_as_unswappable():
    flags = [{"name": "Devin Booker", "slot": "PG", "status": "OUT", "locked": True, "mode": "CASH",
              "rank": 1, "entered": True, "salary_room": 0, "suggestions": []}]
    _, body = prelock.format_alert("2026-11-05", flags, lineups_checked=1)
    assert "tipped" in body.lower() or "locked" in body.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prelock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dfs.prelock'`

- [ ] **Step 3: Write the implementation**

```python
# dfs/prelock.py
"""The pre-lock alert's decisions, kept pure so they can be tested without a clock, a database
or a network.

Why an upper bound rather than a 15-20 minute band: GitHub's scheduler is late by several
minutes and sometimes much more, so consecutive ticks can land at T-22 and then T-14, stepping
clean over any band. Firing on the first tick at or inside the lead, with a once-only marker in
alerts_sent, guarantees an alert no later than roughly 15 minutes out and tolerates the jitter.
Correctness comes from the lock time in the database, never from the cron's hours, so daylight
saving cannot drift it."""
from datetime import datetime, timezone

from .names import name_norm

ALERT_LEAD_MINUTES = 20


def _utc(ts):
    if ts is None:
        return None
    t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def minutes_to_lock(lock_time_utc, now) -> float | None:
    lock, n = _utc(lock_time_utc), _utc(now)
    if lock is None or n is None:
        return None
    return (lock - n).total_seconds() / 60.0


def should_alert(lock_time_utc, now, already_sent: bool, lead_minutes: int = ALERT_LEAD_MINUTES) -> bool:
    if already_sent:
        return False
    m = minutes_to_lock(lock_time_utc, now)
    if m is None:
        return False
    return 0 < m <= lead_minutes


def overlap_ratio(rg_names, dk_names) -> float:
    """Share of the DraftKings pool that RotoGrinders also returned, matched on normalized names.
    The RotoGrinders projections page is slate-scoped, so a low ratio means we were handed a
    different slate's players and must not build from them."""
    dk = {name_norm(n) for n in dk_names if n}
    if not dk:
        return 0.0
    rg = {name_norm(n) for n in rg_names if n}
    return len(dk & rg) / len(dk)


def format_alert(slate_date, flags: list[dict], lineups_checked: int) -> tuple[str, str]:
    """(title, body). The title is an ASCII constant because ntfy sends it as an HTTP header;
    every player name goes in the body, which is UTF-8."""
    swappable = [f for f in flags if not f["locked"]]
    if not flags:
        title = "Lineups clean"
        body = f"{slate_date}: {lineups_checked} lineup(s) checked, no flagged players."
        return title, body
    title = f"{len(flags)} flagged - {len(swappable)} swappable"
    lines = [f"{slate_date}: {lineups_checked} lineup(s) checked."]
    for f in flags:
        who = f"{f['name']} ({f['slot']}, {f['status']})"
        tag = "GPP" if f["mode"] == "GPP" else f["mode"]
        entered = " [ENTERED]" if f.get("entered") else ""
        if f["locked"]:
            lines.append(f"- {who} {tag}#{f['rank']}{entered}: game already tipped, cannot swap")
            continue
        subs = ", ".join(f"{s['name']} ${s['salary']} ({s['proj']:+.1f})" for s in f["suggestions"][:3])
        lines.append(f"- {who} {tag}#{f['rank']}{entered}: {subs or 'no fitting replacement'}")
    return title, "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prelock.py -v`
Expected: all passed. If `test_overlap_ratio_matches_on_normalized_names` fails, print `name_norm("D. Brooks")` and adjust the asserted band to match real behavior — do not weaken `overlap_ratio`.

- [ ] **Step 5: Write the migration**

```sql
-- db/migrations/008_rg_session_alerts.sql
-- The cached RotoGrinders session, so logins stay roughly weekly instead of dozens a night,
-- plus failure bookkeeping so a revoked password cannot be retried every five minutes.
CREATE TABLE IF NOT EXISTS rg_session (
  id              int         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  cookie          text,
  acquired_at     timestamptz,
  failures        int         NOT NULL DEFAULT 0,
  last_failure_at timestamptz
);

-- One alert per slate per kind. The unique constraint is what makes the pre-lock alert fire
-- exactly once across the ~30 evening ticks, even if two runs overlap.
CREATE TABLE IF NOT EXISTS alerts_sent (
  id       bigserial   PRIMARY KEY,
  slate_id bigint      NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  kind     text        NOT NULL,
  sent_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (slate_id, kind)
);
```

- [ ] **Step 6: Write the failing migration test**

```python
# append to tests/test_db.py
def test_migration_008_adds_session_and_alert_tables(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('rg_session') AS a, to_regclass('alerts_sent') AS b")
            r = cur.fetchone()
            assert r["a"] is not None and r["b"] is not None


def test_alerts_sent_allows_only_one_row_per_slate_and_kind(monkeypatch):
    """This unique constraint is the fire-once guarantee for the pre-lock alert."""
    import psycopg
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO slates(date, draft_group_id, slate_key)
                           VALUES ('2026-11-05', 999001, 'A@B') RETURNING id""")
            sid = cur.fetchone()["id"]
            cur.execute("INSERT INTO alerts_sent(slate_id, kind) VALUES (%s,'prelock')", (sid,))
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO alerts_sent(slate_id, kind) VALUES (%s,'prelock')", (sid,))
        conn.rollback()
```

- [ ] **Step 7: Run the migration tests**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_db.py -v`
Expected: all passed. If `slates` rejects the insert because `slate_key` is absent from your schema, read `db/migrations/002_slate_key.sql` and match the real column list — do not drop the test.

- [ ] **Step 8: Commit**

```bash
git add dfs/prelock.py tests/test_prelock.py db/migrations/008_rg_session_alerts.sql tests/test_db.py
git commit -m "Add pre-lock timing decision, slate-overlap check and schema

should_alert fires on the first tick at or inside a 20-minute lead rather
than inside a 15-20 band: GitHub's scheduler jitter can land ticks at
T-22 then T-14 and step over any band. alerts_sent's unique constraint is
the fire-once guarantee. overlap_ratio guards against the slate-scoped
RotoGrinders page handing us the wrong slate's players."
```

---

### Task 3: RotoGrinders login with a cached session and failure backoff

**Files:**
- Modify: `dfs/sources/rg_fetch.py`
- Test: `tests/test_rg_fetch.py`

**Interfaces:**
- Consumes: `prelock.overlap_ratio` (Task 2) is used by callers, not here.
- Produces:
  - `rg_fetch.login(opener_factory=None) -> str` — returns a `Cookie:` header value. Raises `RGNotConnected`.
  - `rg_fetch.session_cookie(conn, login_fn=None, now=None) -> str` — read-through cache over `rg_session`.
  - `rg_fetch.clear_session(conn) -> None`
  - `rg_fetch.fetch_table_text(conn=None, opener_factory=None) -> tuple[str, bytes]` — unchanged return shape `(table_text, gzipped_html)`; now session-aware with exactly one re-login retry.
  - `rg_fetch.MAX_LOGIN_FAILURES = 3`, `rg_fetch.FAILURE_COOLDOWN_MINUTES = 30`
- Preserved: `connected()`, `fetch_html()`, `html_to_table_text()`, `diagnose()` keep working; `RG_COOKIE` still takes precedence as a manual override. `pipeline.run_build` keeps calling `fetch_table_text()` with no arguments.

**Probe facts to encode:** `POST https://rotogrinders.com/sign-in` with form fields `username` and `password`, no CSRF token. Success sets an `rguid` cookie and redirects to `https://rotogrinders.com/`. Logged out the projections table has 7 rows; logged in it has a 47-column header plus data rows.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rg_fetch.py
"""No network. A fake opener stands in for RotoGrinders so the login contract is pinned
without contacting the site."""
from datetime import datetime, timedelta, timezone

import pytest

from dfs.sources import rg_fetch

NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, body, url):
        self._body = body.encode("utf-8")
        self._url = url
        self.headers = {}
        self.status = 200

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Records requests; sets a cookie on a successful sign-in POST."""

    def __init__(self, good_credentials=True, table_rows=60):
        self.good = good_credentials
        self.rows = table_rows
        self.requests = []
        self.cookies = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.requests.append((req.get_method(), url, req.data))
        if url.endswith("/sign-in"):
            if self.good:
                self.cookies = ["rguid=abc123", "rgref=x"]
                return FakeResponse("<html>home</html>", "https://rotogrinders.com/")
            return FakeResponse("<html>Invalid username or password</html>",
                                "https://rotogrinders.com/sign-in")
        rows = "".join(f"<tr><td>P{i}</td>{'<td>1</td>' * 46}</tr>" for i in range(self.rows))
        head = "<thead><tr>" + "".join(f"<th>C{i}</th>" for i in range(47)) + "</tr></thead>"
        return FakeResponse(f"<html><table>{head}{rows}</table></html>",
                            "https://rotogrinders.com/projected-stats/nba?site=draftkings")


def test_login_posts_username_and_password_to_sign_in(monkeypatch):
    monkeypatch.setenv("RG_USER", "someuser")
    monkeypatch.setenv("RG_PASS", "somepass")
    fake = FakeOpener()
    cookie = rg_fetch.login(opener_factory=lambda: fake)
    method, url, data = fake.requests[0]
    assert method == "POST"
    assert url == "https://rotogrinders.com/sign-in"
    assert b"username=someuser" in data and b"password=somepass" in data
    assert "rguid=" in cookie


def test_login_without_credentials_raises_without_contacting_the_site(monkeypatch):
    monkeypatch.delenv("RG_USER", raising=False)
    monkeypatch.delenv("RG_PASS", raising=False)
    fake = FakeOpener()
    with pytest.raises(rg_fetch.RGNotConnected, match="RG_USER"):
        rg_fetch.login(opener_factory=lambda: fake)
    assert fake.requests == []


def test_login_with_bad_credentials_raises_and_never_echoes_the_password(monkeypatch):
    monkeypatch.setenv("RG_USER", "someuser")
    monkeypatch.setenv("RG_PASS", "hunter2-secret")
    fake = FakeOpener(good_credentials=False)
    with pytest.raises(rg_fetch.RGNotConnected) as exc:
        rg_fetch.login(opener_factory=lambda: fake)
    assert "hunter2-secret" not in str(exc.value)


def test_backoff_refuses_to_attempt_after_repeated_failures(monkeypatch):
    """A revoked password must not be retried every five minutes all evening."""
    monkeypatch.setenv("RG_USER", "u")
    monkeypatch.setenv("RG_PASS", "p")
    state = {"cookie": None, "acquired_at": None,
             "failures": rg_fetch.MAX_LOGIN_FAILURES,
             "last_failure_at": NOW - timedelta(minutes=5)}
    attempts = []

    with pytest.raises(rg_fetch.RGNotConnected, match="cooling off"):
        rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: attempts.append(1), now=NOW)
    assert attempts == []


def test_backoff_expires_after_the_cooldown(monkeypatch):
    state = {"cookie": None, "acquired_at": None,
             "failures": rg_fetch.MAX_LOGIN_FAILURES,
             "last_failure_at": NOW - timedelta(minutes=rg_fetch.FAILURE_COOLDOWN_MINUTES + 1)}
    out = rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: "rguid=new", now=NOW)
    assert out == "rguid=new"
    assert state["failures"] == 0


def test_cached_cookie_is_reused_without_logging_in():
    state = {"cookie": "rguid=cached", "acquired_at": NOW - timedelta(days=2),
             "failures": 0, "last_failure_at": None}
    logins = []
    out = rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: logins.append(1), now=NOW)
    assert out == "rguid=cached"
    assert logins == []


def test_missing_cookie_triggers_exactly_one_login_and_is_stored():
    state = {"cookie": None, "acquired_at": None, "failures": 0, "last_failure_at": None}
    logins = []

    def fake_login():
        logins.append(1)
        return "rguid=fresh"

    out = rg_fetch.session_cookie(_FakeConn(state), login_fn=fake_login, now=NOW)
    assert out == "rguid=fresh"
    assert logins == [1]
    assert state["cookie"] == "rguid=fresh"


def test_rg_cookie_env_var_still_overrides_the_cached_session(monkeypatch):
    monkeypatch.setenv("RG_COOKIE", "rguid=manual")
    state = {"cookie": "rguid=cached", "acquired_at": NOW, "failures": 0, "last_failure_at": None}
    assert rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: "x", now=NOW) == "rguid=manual"


class _FakeConn:
    """Minimal stand-in for a psycopg connection over the single rg_session row."""

    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def commit(self):
        pass


class _FakeCursor:
    def __init__(self, state):
        self.state = state
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if s.startswith("select"):
            self._last = dict(self.state)
        elif "set failures = 0" in s or "failures=0" in s:
            self.state.update(cookie=params[0], acquired_at=params[1] if len(params) > 1 else None,
                              failures=0, last_failure_at=None)
        elif "failures = failures + 1" in s or "failures=failures+1" in s:
            self.state["failures"] = self.state.get("failures", 0) + 1
            self.state["last_failure_at"] = params[0] if params else None
        elif s.startswith("update rg_session set cookie = null") or "cookie = null" in s:
            self.state["cookie"] = None

    def fetchone(self):
        return self._last
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rg_fetch.py -v`
Expected: FAIL with `AttributeError: module 'dfs.sources.rg_fetch' has no attribute 'login'`

- [ ] **Step 3: Write the implementation**

Replace the module docstring and add the new functions. Keep `fetch_html`, `html_to_table_text` and `diagnose` as they are, except that `fetch_html` gains an optional `cookie` argument.

```python
# dfs/sources/rg_fetch.py  — new and changed parts
"""Read RotoGrinders' NBA projected-stats page as the user.

The projections table is server-rendered HTML once signed in (probed 2026-11-05: 7 rows signed
out, a 47-column header plus data rows signed in), so a stdlib POST with a cookie jar is enough
and no browser is involved.

Credentials come from RG_USER / RG_PASS (sensitive, Vercel) and are never logged or returned.
The session cookie is cached in rg_session so logins stay roughly weekly rather than dozens a
night, which is the point: a large login footprint is how an account gets flagged. A revoked
password is capped at MAX_LOGIN_FAILURES attempts per FAILURE_COOLDOWN_MINUTES so the
five-minute pre-lock schedule cannot hammer the sign-in form all evening.

RG_COOKIE remains supported and takes precedence, as a manual override when login breaks."""
import gzip
import http.cookiejar
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http import UA
from .uploads import UploadError

PROJ_URL = "https://rotogrinders.com/projected-stats/nba?site=draftkings"
SIGNIN_URL = "https://rotogrinders.com/sign-in"
MAX_LOGIN_FAILURES = 3
FAILURE_COOLDOWN_MINUTES = 30


class RGNotConnected(UploadError):
    pass


def connected() -> bool:
    return bool(os.environ.get("RG_COOKIE", "").strip() or
                (os.environ.get("RG_USER", "").strip() and os.environ.get("RG_PASS", "").strip()))


def _default_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def login(opener_factory=None) -> str:
    """Sign in and return a Cookie header value. Raises RGNotConnected, never echoing the password."""
    user = os.environ.get("RG_USER", "").strip()
    pw = os.environ.get("RG_PASS", "")
    if not user or not pw:
        raise RGNotConnected("RotoGrinders login isn't configured: set RG_USER and RG_PASS in Vercel "
                             "(sensitive), or set RG_COOKIE as a manual override.")
    opener = (opener_factory or _default_opener)()
    data = urllib.parse.urlencode({"username": user, "password": pw}).encode()
    headers = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
               "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9",
               "Referer": SIGNIN_URL}
    try:
        with opener.open(urllib.request.Request(SIGNIN_URL, data=data, headers=headers),
                         timeout=30) as r:
            final = r.geturl()
            r.read()
    except urllib.error.HTTPError as e:
        raise RGNotConnected(f"RotoGrinders answered HTTP {e.code} to the sign-in request") from e
    except Exception as e:  # noqa: BLE001 - must not leak the request body
        raise RGNotConnected(f"RotoGrinders sign-in failed: {type(e).__name__}") from e
    names = getattr(opener, "cookies", None)
    if names is None:
        jar = next((h.cookiejar for h in getattr(opener, "handlers", [])
                    if isinstance(h, urllib.request.HTTPCookieProcessor)), None)
        names = [f"{c.name}={c.value}" for c in (jar or [])]
    if not any(n.startswith("rguid=") for n in names) or "/sign-in" in final:
        raise RGNotConnected("RotoGrinders rejected the sign-in (no session cookie came back). "
                             "Check RG_USER / RG_PASS in Vercel.")
    return "; ".join(names)


def _read_session(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT cookie, acquired_at, failures, last_failure_at FROM rg_session WHERE id=1")
        return cur.fetchone() or {"cookie": None, "acquired_at": None,
                                  "failures": 0, "last_failure_at": None}


def _store_session(conn, cookie, now):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO rg_session(id, cookie, acquired_at, failures, last_failure_at)
                       VALUES (1,%s,%s,0,NULL)
                       ON CONFLICT (id) DO UPDATE SET cookie=EXCLUDED.cookie,
                         acquired_at=EXCLUDED.acquired_at, failures = 0, last_failure_at=NULL""",
                    (cookie, now))
    conn.commit()


def _record_failure(conn, now):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO rg_session(id, failures, last_failure_at) VALUES (1,1,%s)
                       ON CONFLICT (id) DO UPDATE SET failures = failures + 1,
                         last_failure_at = EXCLUDED.last_failure_at""", (now,))
    conn.commit()


def clear_session(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE rg_session SET cookie = NULL WHERE id=1")
    conn.commit()


def _cooling_off(row, now) -> bool:
    if (row.get("failures") or 0) < MAX_LOGIN_FAILURES:
        return False
    last = row.get("last_failure_at")
    if not last:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(minutes=FAILURE_COOLDOWN_MINUTES)


def session_cookie(conn, login_fn=None, now=None) -> str:
    """Cached read-through: the manual RG_COOKIE override, else the stored cookie, else one login."""
    manual = os.environ.get("RG_COOKIE", "").strip()
    if manual:
        return manual
    now = now or datetime.now(timezone.utc)
    row = _read_session(conn)
    if row.get("cookie"):
        return row["cookie"]
    if _cooling_off(row, now):
        raise RGNotConnected(
            f"RotoGrinders sign-in failed {row['failures']} times; cooling off for "
            f"{FAILURE_COOLDOWN_MINUTES} minutes before trying again so the account isn't hammered. "
            "Check RG_USER / RG_PASS in Vercel.")
    try:
        cookie = (login_fn or login)()
    except Exception:
        _record_failure(conn, now)
        raise
    _store_session(conn, cookie, now)
    return cookie
```

Then change `fetch_html` to take the cookie and `fetch_table_text` to manage the session with one retry:

```python
def fetch_html(cookie: str | None = None) -> str:
    cookie = cookie or os.environ.get("RG_COOKIE", "").strip()
    if not cookie:
        raise RGNotConnected("RotoGrinders isn't connected. Set RG_USER and RG_PASS in Vercel, "
                             "or use the grabber button; see /rg.html.")
    req = urllib.request.Request(PROJ_URL, headers={
        "User-Agent": UA, "Accept": "text/html", "Cookie": cookie,
        "Accept-Language": "en-US,en;q=0.9", "Referer": "https://rotogrinders.com/"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            final_url = r.geturl()
    except urllib.error.HTTPError as e:
        raise RGNotConnected(f"RotoGrinders answered HTTP {e.code}; the session may have expired") from e
    html = raw.decode("utf-8", "replace")
    if "/sign-in" in final_url or "/login" in final_url:
        raise RGNotConnected("RotoGrinders sent us to its sign-in page: the session has expired")
    return html


def fetch_table_text(conn=None, opener_factory=None) -> tuple[str, bytes]:
    """(table text for the parser, gzipped page HTML for the archive).

    Opens its own short connection when none is given, because run_build calls this before it
    opens the build's connection. A rejected session is re-acquired exactly once."""
    if conn is None:
        from .. import db
        with db.connect() as own:
            db.migrate(own)
            return fetch_table_text(own, opener_factory=opener_factory)
    lf = (lambda: login(opener_factory=opener_factory)) if opener_factory else None
    cookie = session_cookie(conn, login_fn=lf)
    try:
        html = fetch_html(cookie)
    except RGNotConnected:
        clear_session(conn)
        cookie = session_cookie(conn, login_fn=lf)
        html = fetch_html(cookie)
    return html_to_table_text(html), gzip.compress(html.encode("utf-8"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rg_fetch.py tests/test_rg_table.py -v`
Expected: all passed. `tests/test_rg_table.py:77-80` asserts `fetch_html()` raises when nothing is configured — it must still pass, since `fetch_html` with no cookie and no `RG_COOKIE` still raises `RGNotConnected`.

- [ ] **Step 5: Run the whole suite to confirm nothing regressed**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass or skip.

- [ ] **Step 6: Commit**

```bash
git add dfs/sources/rg_fetch.py tests/test_rg_fetch.py
git commit -m "Read RotoGrinders with an automatic login and a cached session

Probed: POST /sign-in with username/password sets an rguid cookie and the
projections table is then server-rendered HTML, so no browser is needed.

The cookie is cached in rg_session, so logins stay roughly weekly rather
than once per job run - a large login footprint is how an account gets
flagged. Failed logins are capped at 3 per 30 minutes so a revoked
password cannot be retried every five minutes all evening. RG_COOKIE
still overrides, as a manual escape hatch."
```

---

### Task 4: The pre-lock sweep and alert

This is the deliverable you cannot do by hand at 6:45pm. It works on slates built the current manual way, so it ships before the 1pm build exists.

**Files:**
- Modify: `dfs/jobs.py`
- Modify: `api/cron.py`
- Test: `tests/test_jobs_prelock.py`

**Interfaces:**
- Consumes: `prelock.should_alert`, `prelock.format_alert`, `prelock.overlap_ratio` (Task 2); `notify.push` (Task 1); `rg_fetch.fetch_table_text` (Task 3); existing `pipeline.run_refresh`, `pipeline.check`, `jobs._start`, `jobs._finish`.
- Produces: `jobs.prelock(now=None, fetch=None, rg_fetch_fn=None, push=None) -> dict` shaped like the other jobs: `{"ok": bool, "summary": dict, "errors": dict}`. `api/cron.py` accepts `job=prelock`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jobs_prelock.py
"""Pure-logic tests for the pre-lock job's decisions. The database-backed path is exercised by
tests/test_jobs_prelock_db.py when TEST_DATABASE_URL is set."""
from datetime import datetime, timedelta, timezone

from dfs import prelock

LOCK = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)


def test_a_slate_outside_the_lead_is_skipped_without_fetching():
    """The 5-minute schedule must touch only Postgres until the lead window opens."""
    assert prelock.should_alert(LOCK, LOCK - timedelta(minutes=60), already_sent=False) is False


def test_two_slates_the_same_evening_are_decided_independently():
    main, turbo = LOCK, LOCK + timedelta(hours=1)
    now = LOCK - timedelta(minutes=18)
    assert prelock.should_alert(main, now, already_sent=False) is True
    assert prelock.should_alert(turbo, now, already_sent=False) is False
```

```python
# tests/test_jobs_prelock_db.py
import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

LOCK = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)


def _slate(cur, lock_time, date="2026-11-05", dg=990001, key="A@B"):
    cur.execute("""INSERT INTO slates(date, draft_group_id, slate_key, lock_time_utc, n_games)
                   VALUES (%s,%s,%s,%s,1) RETURNING id""", (date, dg, key, lock_time))
    return cur.fetchone()["id"]


def test_alert_fires_once_then_is_suppressed(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import db, jobs
    pushes = []
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            sid = _slate(cur, LOCK, dg=990101, key="C@D")
        conn.commit()

    def fake_refresh(**kw):
        return {"slate_id": sid, "check": {"checked_lineups": 2, "flags": [], "checked_at": "x"}}

    monkeypatch.setattr("dfs.pipeline.run_refresh", fake_refresh)
    out1 = jobs.prelock(now=LOCK - timedelta(minutes=18),
                        rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                        push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    out2 = jobs.prelock(now=LOCK - timedelta(minutes=16),
                        rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                        push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert len(pushes) == 1, "the alert must fire exactly once per slate"
    assert out1["ok"] and out2["ok"]


def test_notifier_failure_does_not_fail_the_job_but_is_recorded(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import db, jobs
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            sid = _slate(cur, LOCK, dg=990102, key="E@F")
        conn.commit()
    monkeypatch.setattr("dfs.pipeline.run_refresh",
                        lambda **kw: {"slate_id": sid,
                                      "check": {"checked_lineups": 1, "flags": [], "checked_at": "x"}})
    out = jobs.prelock(now=LOCK - timedelta(minutes=18),
                       rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                       push=lambda **kw: {"ok": False, "error": "connection refused"})
    assert out["summary"]["alerted"], "the sweep's real work still happened"
    assert any("connection refused" in str(v) for v in out["errors"].values())


def test_sweep_failure_pushes_the_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import db, jobs
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            sid = _slate(cur, LOCK, dg=990103, key="G@H")
        conn.commit()
    pushes = []

    def boom():
        raise RuntimeError("rotogrinders unreachable")

    out = jobs.prelock(now=LOCK - timedelta(minutes=18), rg_fetch_fn=boom,
                       push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is False
    assert pushes, "a failed sweep must still reach the phone"
    assert "unreachable" in pushes[0]["body"]


def test_no_alert_when_lock_time_is_null(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import db, jobs
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            _slate(cur, None, dg=990104, key="I@J")
        conn.commit()
    pushes = []
    out = jobs.prelock(now=LOCK - timedelta(minutes=18),
                       rg_fetch_fn=lambda: ("x", b""),
                       push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert pushes == []
    assert out["ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_jobs_prelock.py tests/test_jobs_prelock_db.py -v`
Expected: the pure tests pass; the DB tests FAIL with `AttributeError: module 'dfs.jobs' has no attribute 'prelock'`

- [ ] **Step 3: Write the implementation**

Add to `dfs/jobs.py`:

```python
def prelock(now: datetime | None = None, fetch=None, rg_fetch_fn=None, push=None) -> dict:
    """Called every five minutes through the evening. Reads today's slates and does nothing until
    a slate is inside the alert lead, so almost every run touches only Postgres. Inside the lead
    it re-reads RotoGrinders and the lineup pages, re-runs the late-scratch check and pushes one
    alert per slate.

    A failed sweep pushes the error: at 6:45pm "the check broke, go look yourself" is useful and
    silence is not."""
    from . import notify, pipeline, prelock as pl
    now = now or datetime.now(timezone.utc)
    push = push or notify.push
    with db.connect() as conn:
        db.migrate(conn)
        rid = _start(conn, "prelock")
        errors, summary = {}, {"considered": 0, "alerted": [], "skipped": []}
        with conn.cursor() as cur:
            cur.execute("""SELECT s.id, s.date, s.lock_time_utc,
                                  EXISTS (SELECT 1 FROM alerts_sent a
                                          WHERE a.slate_id = s.id AND a.kind='prelock') AS sent
                           FROM slates s
                           WHERE s.date BETWEEN %s AND %s
                           ORDER BY s.lock_time_utc NULLS LAST""",
                        (history.et_date(now) - timedelta(days=1), history.et_date(now)))
            slates = cur.fetchall()
        summary["considered"] = len(slates)
        for s in slates:
            if not pl.should_alert(s["lock_time_utc"], now, already_sent=s["sent"]):
                summary["skipped"].append({"slate_id": s["id"],
                                           "minutes_to_lock": pl.minutes_to_lock(s["lock_time_utc"], now)})
                continue
            try:
                rg_text, _ = (rg_fetch_fn or _rg_table_text)()
                out = pipeline.run_refresh(rg_text=rg_text, slate_id=s["id"], fetch=fetch, now=now,
                                           save_raw_pages=False)
                chk = out["check"]
                title, body = pl.format_alert(str(s["date"]), chk["flags"], chk["checked_lineups"])
                res = push(title=title, body=body, priority="high", tags="warning")
                if not res.get("ok"):
                    errors[f"push_{s['id']}"] = res.get("error") or "push failed"
                summary["alerted"].append({"slate_id": s["id"], "flags": len(chk["flags"]),
                                           "swappable": sum(1 for f in chk["flags"] if not f["locked"])})
            except Exception as e:  # noqa: BLE001 - recorded on the run AND pushed
                msg = f"{type(e).__name__}: {e}"[:300]
                errors[f"prelock_{s['id']}"] = msg
                push(title="Pre-lock check FAILED", priority="high", tags="rotating_light",
                     body=f"Slate {s['id']} ({s['date']}): {msg}\nCheck your lineups by hand.")
                continue
            _mark_alerted(conn, s["id"])
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}


def _rg_table_text():
    from .sources import rg_fetch
    return rg_fetch.fetch_table_text()


def _mark_alerted(conn, slate_id):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO alerts_sent(slate_id, kind) VALUES (%s,'prelock')
                       ON CONFLICT (slate_id, kind) DO NOTHING""", (slate_id,))
    conn.commit()
```

Add `timedelta` to the `datetime` import at the top of `dfs/jobs.py`:

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_jobs_prelock.py tests/test_jobs_prelock_db.py -v`
Expected: all passed

- [ ] **Step 5: Wire the cron endpoint**

In `api/cron.py`, after the `grade` branch:

```python
    if job == "prelock":
        out = jobs.prelock()
        return send(h, 500 if out["errors"] else 200, out)
```

- [ ] **Step 6: Run the whole suite**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q`
Expected: all pass or skip

- [ ] **Step 7: Commit**

```bash
git add dfs/jobs.py api/cron.py tests/test_jobs_prelock.py tests/test_jobs_prelock_db.py
git commit -m "Add the pre-lock sweep and phone alert

Runs every five minutes through the evening and does nothing until a
slate is inside the 20-minute lead, so almost every run touches only
Postgres and RotoGrinders sees one read per slate. alerts_sent makes it
fire exactly once. A failed sweep pushes the error rather than staying
quiet: at 6:45pm silence is the worst outcome."
```

---

### Task 5: Main-slate picker that cannot pick a two-game late slate

The live lobby on 2026-11-05 had a 7-game 7:00pm slate at $1,033,857 and a 2-game 9:00pm slate at $1,029,974 — 0.4% apart — and the existing `pick_main_slate()` ranks on prize pool with no date filter, so it would happily call the wrong one the main draw and would also pick tomorrow's slate over today's.

**Files:**
- Modify: `dfs/sources/dk.py`
- Create: `tests/fixtures/dk_lobby_2026-11-05.json` (public DraftKings data; safe to commit)
- Test: `tests/test_dk_slate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `dk.pick_main_slate(lobby: dict, date=None, override_group_id: int | None = None) -> dict | None`. Returns the same shape `classic_groups()` yields. Returns `None` when no group qualifies. `dk.MAIN_WINDOW_ET = (19, 20)`.
- **Breaking change:** `pick_main_slate(lobby)` with no date previously returned the biggest-prize group. It now requires a date to filter on; called with `date=None` it uses today in ET. Grep for callers before changing: `grep -rn "pick_main_slate" api dfs tests`.

- [ ] **Step 1: Capture the fixture**

```bash
.venv/bin/python - <<'PY'
import json, urllib.request
from dfs.http import UA
req = urllib.request.Request("https://www.draftkings.com/lobby/getcontests?sport=NBA",
                             headers={"User-Agent": UA, "Accept": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read())
keep = {"DraftGroups": data["DraftGroups"],
        "Contests": [{"dg": c.get("dg"), "po": c.get("po")} for c in data.get("Contests", [])]}
open("tests/fixtures/dk_lobby_2026-11-05.json", "w").write(json.dumps(keep, indent=1))
print("groups:", len(keep["DraftGroups"]), "contests:", len(keep["Contests"]))
PY
```

If the live lobby no longer contains the near-tie, hand-write the fixture with the four groups recorded in the spec's findings table instead — the near-tie is the whole point of the test.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_dk_slate.py
import json
from datetime import date
from pathlib import Path

import pytest

from dfs.sources import dk

LOBBY = json.loads((Path(__file__).parent / "fixtures" / "dk_lobby_2026-11-05.json").read_text())


def test_picks_the_seven_game_seven_pm_slate_not_the_richer_two_game_late_one():
    """The two top groups were 0.4% apart in prize money and the runner-up was a 2-game 9pm
    slate. Prize pool alone cannot express 'main draw'."""
    got = dk.pick_main_slate(LOBBY, date=date(2026, 11, 6))
    assert got is not None
    assert got["game_count"] == 7
    assert got["start"].astimezone(dk.ET).hour == 19


def test_does_not_return_another_days_slate():
    got = dk.pick_main_slate(LOBBY, date=date(2026, 11, 5))
    if got is not None:
        assert got["start"].astimezone(dk.ET).date() == date(2026, 11, 5)


def test_returns_none_when_no_group_starts_in_the_main_window():
    assert dk.pick_main_slate(LOBBY, date=date(2030, 1, 1)) is None


def test_override_wins_over_ranking():
    groups = dk.classic_groups(LOBBY)
    target = min(groups, key=lambda g: g["prize_pool"])
    got = dk.pick_main_slate(LOBBY, date=date(2026, 11, 6),
                             override_group_id=target["draft_group_id"])
    assert got["draft_group_id"] == target["draft_group_id"]


def test_override_for_a_group_not_in_the_lobby_returns_none():
    assert dk.pick_main_slate(LOBBY, date=date(2026, 11, 6), override_group_id=1) is None


def test_more_games_beats_more_money_inside_the_window():
    lobby = {"DraftGroups": [
        {"DraftGroupId": 1, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
         "GameCount": 8},
        {"DraftGroupId": 2, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:30:00.0000000",
         "GameCount": 3},
    ], "Contests": [{"dg": 1, "po": 10}, {"dg": 2, "po": 5_000_000}]}
    assert dk.pick_main_slate(lobby, date=date(2026, 11, 6))["draft_group_id"] == 1


def test_prize_pool_breaks_a_tie_on_game_count():
    lobby = {"DraftGroups": [
        {"DraftGroupId": 1, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
         "GameCount": 5},
        {"DraftGroupId": 2, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:30:00.0000000",
         "GameCount": 5},
    ], "Contests": [{"dg": 1, "po": 100}, {"dg": 2, "po": 900}]}
    assert dk.pick_main_slate(lobby, date=date(2026, 11, 6))["draft_group_id"] == 2


def test_showdown_and_other_game_types_are_ignored():
    lobby = {"DraftGroups": [
        {"DraftGroupId": 9, "GameTypeId": 96, "StartDateEst": "2026-11-06T19:00:00.0000000",
         "GameCount": 12},
    ], "Contests": [{"dg": 9, "po": 9_000_000}]}
    assert dk.pick_main_slate(lobby, date=date(2026, 11, 6)) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dk_slate.py -v`
Expected: FAIL — `pick_main_slate()` takes 1 positional argument

- [ ] **Step 4: Write the implementation**

In `dfs/sources/dk.py`, add the ET zone and replace `pick_main_slate`:

```python
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MAIN_WINDOW_ET = (19, 20)   # the main draw tips 7:00-8:00pm ET; 7:30 and 8:00 starts are normal


def pick_main_slate(lobby: dict, date=None, override_group_id: int | None = None) -> dict | None:
    """The main draw for a given ET date: the Classic group with the most games starting between
    7:00pm and 8:00pm ET, prize pool breaking ties only.

    Prize pool alone is not safe. On 2026-11-05 the richest Classic group was a 7-game 7:00pm
    slate at $1,033,857 and the runner-up a 2-game 9:00pm slate at $1,029,974 - 0.4% apart. The
    lobby also carries several days at once, so an unfiltered pick can return tomorrow's slate.
    Returns None rather than guessing when nothing qualifies: a dark or late-only night is normal."""
    groups = classic_groups(lobby)
    if override_group_id is not None:
        return next((g for g in groups if g["draft_group_id"] == override_group_id), None)
    from datetime import datetime as _dt
    target = date or _dt.now(ET).date()
    lo, hi = MAIN_WINDOW_ET
    eligible = []
    for g in groups:
        start = g.get("start")
        if not start:
            continue
        local = start.astimezone(ET)
        if local.date() == target and lo <= local.hour <= hi:
            eligible.append(g)
    if not eligible:
        return None
    return max(eligible, key=lambda g: ((g.get("game_count") or 0), g["prize_pool"]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dk_slate.py -v`
Expected: all passed

- [ ] **Step 6: Check for existing callers and the whole suite**

```bash
grep -rn "pick_main_slate" api dfs tests
.venv/bin/python -m pytest -q
```

Expected: every caller either passes a date or relies on the ET-today default; suite green.

- [ ] **Step 7: Commit**

```bash
git add dfs/sources/dk.py tests/test_dk_slate.py tests/fixtures/dk_lobby_2026-11-05.json
git commit -m "Pick the main slate by games in the 7-8pm ET window, not by prize pool

The live 2026-11-05 lobby had a 7-game 7pm slate at \$1,033,857 and a
2-game 9pm slate at \$1,029,974 - 0.4% apart - and the old ranking had no
date filter, so it could pick a 2-game late slate, or tomorrow's slate,
as the main draw. Returns None rather than guessing when nothing starts
in the window; a dark night is normal, not a failure."
```

---

### Task 6: The 1pm build job

**Files:**
- Modify: `dfs/jobs.py`
- Modify: `api/cron.py`
- Test: `tests/test_jobs_build.py`

**Interfaces:**
- Consumes: `dk.pick_main_slate` (Task 5), `dk.fetch_lobby`, `prelock.overlap_ratio` (Task 2), `notify.push` (Task 1), `rg_fetch.fetch_table_text` (Task 3), existing `pipeline.run_build`, `pipeline._stored_upload`.
- Produces: `jobs.build(now=None, lobby_fn=None, rg_fetch_fn=None, push=None, build_fn=None) -> dict`. `api/cron.py` accepts `job=build`.
- `jobs.MIN_RG_DK_OVERLAP = 0.6`

**Behavior, from the spec:** identify today's main slate; if a stored DKSalaries upload exists for it, fetch RotoGrinders and build, then push "lineups ready"; if the upload is missing, push "upload DKSalaries.csv" at high priority and stop without error; if no group qualifies, push "no main slate tonight" at low priority and record an ok run.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jobs_build.py
import os
from datetime import date, datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

NOW = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)  # 1:00pm ET

LOBBY_MAIN = {"DraftGroups": [
    {"DraftGroupId": 156184, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
     "GameCount": 7}], "Contests": [{"dg": 156184, "po": 1_000_000}]}
LOBBY_DARK = {"DraftGroups": [
    {"DraftGroupId": 156191, "GameTypeId": 70, "StartDateEst": "2026-11-06T22:00:00.0000000",
     "GameCount": 2}], "Contests": [{"dg": 156191, "po": 400_000}]}


def test_no_qualifying_slate_pushes_a_low_priority_note_and_is_not_a_failure(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import jobs
    pushes = []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_DARK,
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is True
    assert pushes and pushes[0]["priority"] == "low"
    assert "no main slate" in pushes[0]["body"].lower()


def test_missing_dk_upload_pushes_a_nudge_and_does_not_build(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import jobs
    pushes, builds = [], []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     build_fn=lambda **kw: builds.append(kw),
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert builds == []
    assert out["ok"] is True
    assert pushes[0]["priority"] == "high"
    assert "dksalaries" in pushes[0]["body"].lower()


def test_builds_and_pushes_ready_when_the_upload_is_present(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "Position,Name,ID,Salary,Game Info,TeamAbbrev\n")
    monkeypatch.setattr(jobs, "_rg_names", lambda text: ["Devin Booker"])
    monkeypatch.setattr(jobs, "_dk_names", lambda text: ["Devin Booker"])
    pushes = []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=lambda **kw: {"slate_id": 7, "run_id": 3, "n_players": 180},
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is True
    assert out["summary"]["slate_id"] == 7
    assert any("ready" in p["title"].lower() for p in pushes)


def test_refuses_to_build_when_rotogrinders_returned_a_different_slate(monkeypatch):
    """The projections page is slate-scoped. Building from the wrong slate's numbers is the most
    dangerous failure in this design, so a low name overlap must stop the build."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "x")
    monkeypatch.setattr(jobs, "_dk_names", lambda text: ["Devin Booker", "Jalen Green", "Dillon Brooks"])
    monkeypatch.setattr(jobs, "_rg_names", lambda text: ["Nikola Jokic", "Jamal Murray"])
    pushes, builds = [], []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=lambda **kw: builds.append(kw),
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert builds == [], "must not build from another slate's projections"
    assert out["ok"] is False
    assert any("overlap" in str(v).lower() or "slate" in str(v).lower() for v in out["errors"].values())
    assert pushes, "the refusal must reach the phone"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_jobs_build.py -v`
Expected: FAIL with `AttributeError: module 'dfs.jobs' has no attribute 'build'`

- [ ] **Step 3: Write the implementation**

Add to `dfs/jobs.py`:

```python
MIN_RG_DK_OVERLAP = 0.6


def _stored_dk_text(conn, slate_date):
    """The most recent DKSalaries upload for a slate on this ET date, or None."""
    with conn.cursor() as cur:
        cur.execute("""SELECT x.raw FROM source_snapshots x JOIN slates s ON s.id = x.slate_id
                       WHERE s.date = %s AND x.source = 'dk_salaries_csv' AND x.raw IS NOT NULL
                       ORDER BY x.id DESC LIMIT 1""", (slate_date,))
        r = cur.fetchone()
    return r["raw"].tobytes().decode("utf-8", "replace") if r else None


def _dk_names(dk_text):
    from .sources.uploads import parse_dk_salaries
    return [p["name"] for p in parse_dk_salaries(dk_text)["players"]]


def _rg_names(rg_text):
    from .sources.rg_table import parse
    return [r["name"] for r in parse(rg_text)["rows"]]


def build(now: datetime | None = None, lobby_fn=None, rg_fetch_fn=None, push=None,
          build_fn=None, override_group_id=None) -> dict:
    """The 1pm job. Identifies today's main slate and builds from the stored DKSalaries upload
    plus freshly read RotoGrinders projections.

    DraftKings salaries stay a manual upload by decision (the draftables API blocks ordinary
    clients and automating a real-money login was rejected), so the single manual step is
    *prompted* here rather than remembered: a missing upload pushes a nudge with hours of runway
    before lock."""
    from . import notify, pipeline, prelock as pl
    from .sources import dk
    now = now or datetime.now(timezone.utc)
    push = push or notify.push
    today = history.et_date(now)
    with db.connect() as conn:
        db.migrate(conn)
        db.seed_params(conn)
        rid = _start(conn, "build")
        errors, summary = {}, {"date": str(today)}
        try:
            lobby = (lobby_fn or dk.fetch_lobby)()
            main = dk.pick_main_slate(lobby, date=today, override_group_id=override_group_id)
            if not main:
                summary["main_slate"] = None
                push(title="No main slate tonight", priority="low", tags="zzz",
                     body=f"{today}: no Classic group starts 7-8pm ET. Nothing to build.")
                _finish(conn, rid, True, summary, errors)
                return {"ok": True, "summary": summary, "errors": errors}
            summary["main_slate"] = {"draft_group_id": main["draft_group_id"],
                                     "games": main.get("game_count"),
                                     "start": str(main.get("start"))}
            dk_text = _stored_dk_text(conn, today)
            if not dk_text:
                summary["blocked"] = "no dk_salaries_csv upload"
                push(title="Upload DKSalaries.csv", priority="high", tags="warning",
                     body=(f"{today}: main slate is {main.get('game_count')} games at "
                           f"{main.get('start')}, but no DKSalaries.csv is stored yet. "
                           "Upload it and the build will run."))
                _finish(conn, rid, True, summary, errors)
                return {"ok": True, "summary": summary, "errors": errors}
            rg_text, _ = (rg_fetch_fn or _rg_table_text)()
            overlap = pl.overlap_ratio(_rg_names(rg_text), _dk_names(dk_text))
            summary["rg_dk_overlap"] = round(overlap, 3)
            if overlap < MIN_RG_DK_OVERLAP:
                msg = (f"RotoGrinders returned players matching only {overlap:.0%} of the "
                       f"DraftKings pool (need {MIN_RG_DK_OVERLAP:.0%}); refusing to build from "
                       "what looks like a different slate")
                errors["slate_overlap"] = msg
                push(title="Build refused - wrong slate", priority="high", tags="rotating_light",
                     body=f"{today}: {msg}. Build by hand from the page.")
                _finish(conn, rid, False, summary, errors)
                return {"ok": False, "summary": summary, "errors": errors}
            out = (build_fn or pipeline.run_build)(dk_text=dk_text, rg_text=rg_text)
            summary.update({k: out[k] for k in ("slate_id", "run_id") if k in (out or {})})
            res = push(title="Lineups ready", priority="default", tags="basketball",
                       body=(f"{today}: built for the {main.get('game_count')}-game slate. "
                             f"{summary.get('rg_dk_overlap', 0):.0%} pool match. Review and enter."))
            if not res.get("ok"):
                errors["push"] = res.get("error") or "push failed"
        except Exception as e:  # noqa: BLE001 - recorded on the run AND pushed
            msg = f"{type(e).__name__}: {e}"[:300]
            errors["build"] = msg
            push(title="1pm build FAILED", priority="high", tags="rotating_light",
                 body=f"{today}: {msg}\nBuild by hand before lock.")
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_jobs_build.py -v`
Expected: all passed

- [ ] **Step 5: Wire the cron endpoint**

In `api/cron.py`, after the `prelock` branch:

```python
    if job == "build":
        out = jobs.build()
        return send(h, 500 if out["errors"] else 200, out)
```

- [ ] **Step 6: Run the whole suite**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q`
Expected: all pass or skip

- [ ] **Step 7: Commit**

```bash
git add dfs/jobs.py api/cron.py tests/test_jobs_build.py
git commit -m "Add the 1pm build job

Identifies today's main slate, builds from the stored DKSalaries upload
plus freshly read RotoGrinders projections, and prompts for the upload
rather than relying on memory.

Refuses to build when RotoGrinders' players overlap the DraftKings pool
by less than 60 percent: the projections page is slate-scoped, and a
silent wrong-slate build is the worst failure this design can produce."
```

---

### Task 7: Schedules, configuration and documentation

**Files:**
- Create: `.github/workflows/nightly.yml`
- Modify: `PLAN.md`
- Modify: `public/rg.html`
- Modify: `README.md`

**Interfaces:**
- Consumes: `api/cron.py` accepting `job=build` and `job=prelock`.
- Produces: no code interfaces.

**Note on the cron hours:** the workflow schedule is UTC, so a fixed ET hour range drifts by one hour at the daylight-saving boundary. The window is deliberately wider than needed and correctness comes from `should_alert` reading the real lock time; over-polling is free because the repository is public and Actions minutes are unlimited.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/nightly.yml
# Drives the two new jobs. Vercel Hobby cron runs only once a day, so this free GitHub schedule
# is the clock. Needs the same three repository secrets as snapshots.yml: APP_URL, CRON_SECRET,
# VERCEL_BYPASS_SECRET. No RotoGrinders credentials here - those live only in Vercel.
name: nightly
on:
  schedule:
    - cron: "5 18 * * *"          # ~1pm ET build (1pm EST; 2pm EDT - harmless, the job is idempotent)
    - cron: "*/5 22-23 * * *"     # pre-lock sweep, covers 6-7pm ET in EDT and 5-6pm ET in EST
    - cron: "*/5 0-2 * * *"       # ...continues through 9pm ET in both offsets
  workflow_dispatch:
    inputs:
      job:
        description: "job to run (build or prelock)"
        required: true
        default: prelock
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 6
    steps:
      - name: Pick the job for this trigger
        id: pick
        env:
          SCHEDULE: ${{ github.event.schedule }}
          MANUAL: ${{ github.event.inputs.job }}
        run: |
          if [ -n "$MANUAL" ]; then echo "job=$MANUAL" >> "$GITHUB_OUTPUT"
          elif [ "$SCHEDULE" = "5 18 * * *" ]; then echo "job=build" >> "$GITHUB_OUTPUT"
          else echo "job=prelock" >> "$GITHUB_OUTPUT"; fi
      - name: Call the job
        env:
          APP_URL: ${{ secrets.APP_URL }}
          CRON_SECRET: ${{ secrets.CRON_SECRET }}
          BYPASS: ${{ secrets.VERCEL_BYPASS_SECRET }}
          JOB: ${{ steps.pick.outputs.job }}
        run: |
          if [ -z "$APP_URL" ] || [ -z "$CRON_SECRET" ] || [ -z "$BYPASS" ]; then
            echo "::error::APP_URL, CRON_SECRET and VERCEL_BYPASS_SECRET must all be set"; exit 1
          fi
          APP_URL="${APP_URL%/}"
          code=$(curl -sS -o out.json -D headers.txt -w '%{http_code}' --max-time 290 \
            -H "Authorization: Bearer $CRON_SECRET" -H "x-vercel-protection-bypass: $BYPASS" \
            "$APP_URL/api/cron?job=$JOB")
          cat out.json; echo
          case "$code" in
            200) ;;
            302|307|401|403)
              loc=$(grep -i '^location:' headers.txt | tr -d '\r' || true)
              if grep -qi 'sso-api\|vercel.com/login' headers.txt; then
                echo "::error::Vercel's login wall rejected the request: VERCEL_BYPASS_SECRET does not match the project's Protection Bypass for Automation value ($loc)"
              elif [ "$code" = "401" ]; then
                echo "::error::The app rejected CRON_SECRET: the GitHub secret must equal the CRON_SECRET env var in Vercel"
              else
                echo "::error::$JOB returned HTTP $code $loc"
              fi
              exit 1 ;;
            *) echo "::error::$JOB returned HTTP $code"; exit 1 ;;
          esac
```

- [ ] **Step 2: Verify the workflow parses**

```bash
.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/nightly.yml')); print('yaml ok')"
```

Expected: `yaml ok`. If PyYAML is absent, `.venv/bin/pip install pyyaml` first; it is a dev-only check, so do not add it to `requirements.txt`.

- [ ] **Step 3: Update `PLAN.md`**

Replace the `#13 Automation` line. The old text said uploads stay manual with no stored credentials; that is now true of DraftKings only.

```markdown
- [x] #13 Automation: RotoGrinders is read with a stored login (RG_USER/RG_PASS in Vercel,
      sensitive; session cookie cached in rg_session so logins stay roughly weekly, failures
      capped at 3 per 30 minutes). DraftKings salaries stay a manual upload on purpose: the
      draftables API blocks ordinary clients and automating a real-money DK login was rejected.
      The 1pm build prompts for that upload by push notification instead of relying on memory.
      Jobs: build (1pm ET), snapshot (every 20 min), prelock (every 5 min in the evening, alerts
      15-20 min before lock). Every job reports failures to the phone and to job_runs.
```

- [ ] **Step 4: Update the `#14` note in `PLAN.md`**

It is no longer hypothetical — mark it confirmed:

```markdown
- [x] #14 The RG projections page IS parsed. Confirmed live 2026-11-05: the table is
      server-rendered HTML once signed in. Split each <tr> innerText on tabs (not
      querySelectorAll('td')); FPTS=28, FPTS/$=29, pOWN=30, FLOOR=33, CEIL=34, SIM85=44;
      "-" = no tag. Columns are still located by header name, never by position.
```

- [ ] **Step 5: Document the environment variables in `README.md`**

Add under Local development:

```markdown
## Configuration (Vercel environment variables, all sensitive)
- `RG_USER`, `RG_PASS` — RotoGrinders login. Use a password unique to RotoGrinders.
- `NTFY_TOPIC` — an unguessable random string; on ntfy.sh the topic name is the only secret.
  Subscribe to it in the ntfy phone app. Optional `NTFY_SERVER` for a self-hosted instance.
- `RG_COOKIE` — optional manual override if login breaks; takes precedence over the stored session.
- `CRON_SECRET` — must equal the GitHub Actions repository secret of the same name.

GitHub Actions repository secrets: `APP_URL`, `CRON_SECRET`, `VERCEL_BYPASS_SECRET`.
No RotoGrinders credentials are stored in GitHub.
```

- [ ] **Step 6: Update `public/rg.html`**

The page currently walks through copying a session cookie out of devtools. Replace the whole
`<section>` whose heading begins "Automatic: connect your RotoGrinders login" with the markup
below. Leave the "Backup: the grabber button" section exactly as it is — it is the fallback when
login breaks, and removing it would leave no manual path.

```html
  <section>
    <h2>Automatic: your RotoGrinders login (one-time setup)</h2>
    <p>The optimizer signs in to RotoGrinders itself and reads the projections table. No cookie to
      copy, and nothing to redo every week.</p>
    <ol>
      <li>Change your RotoGrinders password to one you use <b>nowhere else</b>.</li>
      <li>Vercel → <b>dfs-optimizer</b> → Settings → Environment Variables → add
        <code>RG_USER</code> (your RotoGrinders username) and <code>RG_PASS</code>, each ticked
        <b>Sensitive</b>, <b>Production</b> and <b>Preview</b> → Save → redeploy production.</li>
      <li>Add <code>NTFY_TOPIC</code> the same way: a long random string you invent. Install the
        <b>ntfy</b> app on your phone and subscribe to that exact string — it is how the
        pre-lock alert reaches you, and the topic name is its only secret.</li>
    </ol>
    <p class="muted">The session is cached, so sign-ins happen roughly weekly rather than on every
      run. If the password is wrong, sign-in is attempted at most 3 times per 30 minutes rather
      than every five minutes, so a bad password cannot hammer your account — builds will say so,
      and the button below still works meanwhile. <code>RG_COOKIE</code> is still honored and
      overrides the stored session if you ever need to paste one by hand.</p>
    <p class="muted">Automated reading of your own account may go against RotoGrinders' terms of
      service; that choice is yours.</p>
  </section>
```

- [ ] **Step 7: Run the whole suite one last time**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q`
Expected: all pass or skip

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/nightly.yml PLAN.md README.md public/rg.html
git commit -m "Schedule the build and pre-lock jobs; document configuration

The cron hours are UTC and deliberately wider than the ET window, because
a fixed hour range drifts an hour at the daylight-saving boundary.
Correctness comes from should_alert reading the real lock time; extra
polling is free on a public repo and touches only Postgres.

PLAN.md #13 is rewritten: 'no stored credentials' was true of both
sources and is now true of DraftKings only, with the reasoning recorded.
#14 is confirmed rather than hypothetical."
```

---

## Deployment checklist (after the last commit, before trusting a night)

- [ ] Add `RG_USER`, `RG_PASS`, `NTFY_TOPIC` in Vercel as **Sensitive**, Production + Preview.
- [ ] Install the ntfy app and subscribe to the `NTFY_TOPIC` string.
- [ ] Confirm the GitHub repository secrets `APP_URL`, `CRON_SECRET`, `VERCEL_BYPASS_SECRET` exist (`snapshots.yml` already needs them).
- [ ] Merge to `main` so Vercel deploys to production.
- [ ] Trigger the workflow by hand with `job=prelock` and confirm a push arrives and a `job_runs` row appears on the slate page.
- [ ] Trigger by hand with `job=build` and confirm the "upload DKSalaries.csv" nudge arrives.
- [ ] **Verify on a real main-slate night** (spec open question 1): upload DKSalaries.csv, run `job=build`, and check `summary.rg_dk_overlap` is high. A low number means the RotoGrinders page is serving a different slate and the slate-selection question needs solving before the 1pm build can be trusted unattended.
