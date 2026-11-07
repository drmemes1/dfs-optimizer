# Recent Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull a projection partway back toward the player's recent DK points per minute when RotoGrinders' number requires an implausible jump, and show the gap.

**Architecture:** A new `player_games` table holds one verified box-score line per player per night, ingested from the same ESPN fetch the grader already does. `dfs/gamelogs.py` writes it and reads back per-player rates. A seventh signal component, `m_form`, turns the gap between the implied rate and the recent rate into a capped multiplier, stored per build like every other signal so grading can measure it.

**Tech Stack:** Python 3.12, psycopg 3, Postgres, ESPN public API (already used), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-recent-form-design.md`

## Global Constraints

- **No new runtime dependencies.** `requirements.txt` stays `numpy`, `scipy`, `psycopg[binary]`, `beautifulsoup4`.
- **Only verified games are written to `player_games`.** Same rule the grader uses: a wrong box score would poison a rate for ten days.
- **The pool rule is untouched.** `PLAN.md` #9 is binding: `salary >= 5500 OR consensus starter OR 24+ minutes`. Recent form scales a projection; it never removes a player from the pool.
- **`m_form = 1.0` whenever the signal has nothing to say.** No data, no opinion — never a guess.
- **`contingent` players are always excluded** from the form adjustment. Their role is changing, so the recent rate describes a job they no longer have.
- **Caps are asymmetric:** cut at most to `form_max_cut` (0.85), boost at most to `form_max_boost` (1.08). RotoGrinders already chases hot play, so the boost side is the side more likely to double-count.
- Test fixtures stay synthetic; the repository is public.
- Every new parameter lives in the `signals` block so the learner can move it. None are frozen.

## Review Focus

Failure modes the spec implies that would otherwise ship untested. Each has a test in the task owning the code.

1. **A rate built from garbage time.** A deep bench player with a freak 20-point, 8-minute night has a huge per-minute rate, and the *boost* branch would inflate his projection. Expected: `form_min_minutes` keeps him out, and if he clears it the boost cap bounds the damage. *Test in Task 4.*
2. **Two players sharing a normalised name.** `name_norm` collisions would attribute one player's games to another and silently corrupt his rate. Expected: an ambiguous name is skipped and reported, never guessed. *Test in Task 3.*
3. **No game logs at all.** On the first run the table is empty. Expected: every player gets exactly 1.0, the build succeeds unchanged, and the report says how many players had no history. *Test in Task 5.*
4. **A game played on the slate's own date.** Rates must use games strictly *before* the slate date, or a player's own evening performance leaks into the projection that was supposed to predict it. Expected: same-date games are excluded. *Test in Task 3.*
5. **Zero minutes across the lookback.** Every game a DNP gives a zero denominator. Expected: no division by zero, `m_form = 1.0`. *Test in Task 4.*

---

### Task 1: Schema

**Files:**
- Create: `db/migrations/009_player_games.sql`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `player_games`, column `pool.m_form numeric`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_db.py
def test_migration_009_adds_player_games_and_the_form_column(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('player_games') AS t")
            assert cur.fetchone()["t"] is not None
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_name='pool' AND column_name='m_form'""")
            assert cur.fetchone() is not None


def test_player_games_holds_one_row_per_player_per_night(monkeypatch):
    """A re-ingest of the same night must update, not duplicate."""
    import psycopg
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO players(name, name_norm) VALUES ('Log Test','log test') RETURNING id")
            pid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO player_games(player_id, game_date, min, dk_pts)
                           VALUES (%s,'2026-10-01',30,40)""", (pid,))
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO player_games(player_id, game_date, min, dk_pts)
                               VALUES (%s,'2026-10-01',31,41)""", (pid,))
        conn.rollback()
```

- [ ] **Step 2: Run to verify it fails**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_db.py -q`
Expected: FAIL — `to_regclass('player_games')` is None.

- [ ] **Step 3: Write the migration**

```sql
-- db/migrations/009_player_games.sql
-- One verified box-score line per player per night. Written only for games that passed the same
-- verification the grader uses: a wrong box score would poison a player's rate for ten days.
CREATE TABLE IF NOT EXISTS player_games (
  player_id  bigint  NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  game_date  date    NOT NULL,
  team       text,
  opp        text,
  min        numeric NOT NULL DEFAULT 0,
  dk_pts     numeric NOT NULL DEFAULT 0,
  pts int, fg3m int, reb int, ast int, stl int, blk int, tov int,
  dnp        boolean NOT NULL DEFAULT false,
  source     text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (player_id, game_date)
);
CREATE INDEX IF NOT EXISTS player_games_player_date_idx ON player_games (player_id, game_date DESC);

-- the recent-form multiplier, stored per build like every other signal so grading measures it
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_form numeric;
```

- [ ] **Step 4: Run to verify it passes**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_db.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/009_player_games.sql tests/test_db.py
git commit -m "Add player_games and the pool.m_form column"
```

---

### Task 2: Game-log ingest and backfill

**Files:**
- Create: `dfs/gamelogs.py`
- Create: `tests/test_gamelogs_db.py`

**Interfaces:**
- Consumes: `boxscores.fetch_day(game_date) -> {"games": [...], "bref": {...}, "boxes": {event_id: {team: [rows]}}}`; `boxscores.verify_game(game, box, bref) -> [problems]`; `scoring.dk_points(pts, fg3m, reb, ast, stl, blk, tov, dnp) -> float`; `names.NameIndex`.
- Produces:
  - `gamelogs.ingest_day(conn, game_date, day) -> {"written": int, "skipped_games": {label: problems}, "unmatched": [names], "ambiguous": [names]}`
  - `gamelogs.backfill(conn, through_date, days, fetch_day=None) -> {"days": {date: summary}, "errors": {date: str}}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gamelogs_db.py
"""Game-log ingest. Uses hand-built ESPN-shaped payloads; nothing is fetched."""
import datetime
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

D = datetime.date(2026, 10, 20)


def _day(verified=True):
    """One finished game, OKC at CLE, with two box lines per side."""
    box = {
        "OKC": [{"name": "Alpha One", "starter": True, "dnp": False, "min": 36, "pts": 30,
                 "fg3m": 4, "reb": 5, "ast": 6, "stl": 2, "blk": 1, "tov": 3},
                {"name": "Beta Two", "starter": False, "dnp": False, "min": 12, "pts": 6,
                 "fg3m": 0, "reb": 2, "ast": 1, "stl": 0, "blk": 0, "tov": 1}],
        "CLE": [{"name": "Gamma Three", "starter": True, "dnp": False, "min": 34, "pts": 24,
                 "fg3m": 2, "reb": 8, "ast": 4, "stl": 1, "blk": 1, "tov": 2},
                {"name": "Delta Four", "starter": False, "dnp": True, "min": 0, "pts": 0,
                 "fg3m": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "tov": 0}],
    }
    game = {"event_id": "401", "teams": {"OKC": {"score": 118, "home": False},
                                         "CLE": {"score": 120, "home": True}},
            "final": True, "period": 4}
    return {"games": [game], "boxes": {"401": box},
            "bref": {} if verified else {"unverifiable": True}}


def _players(conn, names):
    from dfs.names import name_norm
    ids = {}
    with conn.cursor() as cur:
        for n in names:
            cur.execute("INSERT INTO players(name, name_norm) VALUES (%s,%s) RETURNING id",
                        (n, name_norm(n)))
            ids[n] = cur.fetchone()["id"]
    conn.commit()
    return ids


def test_ingest_writes_a_row_per_player_with_dk_points(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    from dfs.scoring import dk_points
    with db.connect() as conn:
        db.migrate(conn)
        ids = _players(conn, ["Alpha One", "Beta Two", "Gamma Three", "Delta Four"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game", lambda g, b, r: [])
        out = gamelogs.ingest_day(conn, D, _day())
        assert out["written"] == 4
        with conn.cursor() as cur:
            cur.execute("SELECT min, dk_pts, dnp FROM player_games WHERE player_id=%s AND game_date=%s",
                        (ids["Alpha One"], D))
            r = cur.fetchone()
        assert float(r["min"]) == 36
        assert float(r["dk_pts"]) == dk_points(pts=30, fg3m=4, reb=5, ast=6, stl=2, blk=1, tov=3)
        assert r["dnp"] is False


def test_ingest_skips_an_unverified_game(monkeypatch):
    """A box score that fails verification must never reach the log."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _players(conn, ["Eps Five"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game",
                            lambda g, b, r: ["minutes do not add up"])
        out = gamelogs.ingest_day(conn, datetime.date(2026, 10, 21), _day())
        assert out["written"] == 0
        assert out["skipped_games"]


def test_ingest_is_idempotent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _players(conn, ["Zed One", "Zed Two", "Zed Three", "Zed Four"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game", lambda g, b, r: [])
        day = _day()
        for t in day["boxes"]["401"]["OKC"]:
            t["name"] = "Zed One" if t["name"] == "Alpha One" else "Zed Two"
        for t in day["boxes"]["401"]["CLE"]:
            t["name"] = "Zed Three" if t["name"] == "Gamma Three" else "Zed Four"
        d2 = datetime.date(2026, 10, 22)
        gamelogs.ingest_day(conn, d2, day)
        gamelogs.ingest_day(conn, d2, day)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM player_games WHERE game_date=%s", (d2,))
            assert cur.fetchone()["n"] == 4


def test_backfill_walks_back_and_records_per_day(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _players(conn, ["Back One", "Back Two", "Back Three", "Back Four"])
        monkeypatch.setattr("dfs.sources.boxscores.verify_game", lambda g, b, r: [])
        seen = []

        def fake_fetch_day(d):
            seen.append(d)
            return {"games": [], "boxes": {}, "bref": {}}

        out = gamelogs.backfill(conn, datetime.date(2026, 10, 30), days=3, fetch_day=fake_fetch_day)
        assert seen == [datetime.date(2026, 10, 29), datetime.date(2026, 10, 28),
                        datetime.date(2026, 10, 27)], "walks back from the day before"
        assert len(out["days"]) == 3


def test_backfill_records_a_failing_day_without_stopping(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        calls = {"n": 0}

        def flaky(d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("espn down")
            return {"games": [], "boxes": {}, "bref": {}}

        out = gamelogs.backfill(conn, datetime.date(2026, 11, 1), days=3, fetch_day=flaky)
        assert out["errors"], "the bad day is recorded"
        assert len(out["days"]) == 2, "the other days still ran"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_gamelogs_db.py -q`
Expected: FAIL — `No module named 'dfs.gamelogs'`.

- [ ] **Step 3: Write the implementation**

```python
# dfs/gamelogs.py
"""Per-player game logs, built from the same verified ESPN box scores the grader uses.

Only verified games are written. An unverified box score would poison a player's recent rate for
as long as it sits in the lookback, which is exactly the silent corruption PLAN.md's
box-score verification rule exists to prevent."""
from datetime import timedelta

from .names import NameIndex, name_norm
from .scoring import dk_points
from .sources import boxscores


def _player_index(conn):
    """Every known player, for matching box-score names. Players enter this table when they first
    appear on a slate, so a name that matches nobody is simply someone we have never drafted."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, name_norm, last_team FROM players")
        rows = cur.fetchall()
    by_norm = {}
    for r in rows:
        by_norm.setdefault(r["name_norm"], []).append(r)
    return by_norm


def ingest_day(conn, game_date, day) -> dict:
    """Write one row per player line in every verified game on `game_date`."""
    by_norm = _player_index(conn)
    out = {"written": 0, "skipped_games": {}, "unmatched": [], "ambiguous": []}
    rows = []
    for g in day.get("games", []):
        label = "@".join(sorted(g["teams"]))
        box = day.get("boxes", {}).get(g["event_id"], {})
        problems = boxscores.verify_game(g, box, day.get("bref") or {})
        if problems:
            out["skipped_games"][label] = problems
            continue
        teams = list(g["teams"])
        for team, lines in box.items():
            opp = next((t for t in teams if t != team), None)
            for line in lines:
                cands = by_norm.get(name_norm(line.get("name") or ""), [])
                if not cands:
                    out["unmatched"].append(line.get("name"))
                    continue
                if len(cands) > 1:
                    # two players share a normalised name; attributing games to the wrong one
                    # would silently corrupt a rate, so neither gets the game
                    same_team = [c for c in cands if c["last_team"] == team]
                    if len(same_team) != 1:
                        out["ambiguous"].append(line.get("name"))
                        continue
                    cands = same_team
                rows.append((cands[0]["id"], game_date, team, opp,
                             line.get("min") or 0,
                             dk_points(pts=line.get("pts") or 0, fg3m=line.get("fg3m") or 0,
                                       reb=line.get("reb") or 0, ast=line.get("ast") or 0,
                                       stl=line.get("stl") or 0, blk=line.get("blk") or 0,
                                       tov=line.get("tov") or 0, dnp=bool(line.get("dnp"))),
                             line.get("pts") or 0, line.get("fg3m") or 0, line.get("reb") or 0,
                             line.get("ast") or 0, line.get("stl") or 0, line.get("blk") or 0,
                             line.get("tov") or 0, bool(line.get("dnp")), "espn"))
    if rows:
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO player_games(player_id, game_date, team, opp, min, dk_pts,
                                 pts, fg3m, reb, ast, stl, blk, tov, dnp, source)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (player_id, game_date) DO UPDATE SET
                                 team=EXCLUDED.team, opp=EXCLUDED.opp, min=EXCLUDED.min,
                                 dk_pts=EXCLUDED.dk_pts, pts=EXCLUDED.pts, fg3m=EXCLUDED.fg3m,
                                 reb=EXCLUDED.reb, ast=EXCLUDED.ast, stl=EXCLUDED.stl,
                                 blk=EXCLUDED.blk, tov=EXCLUDED.tov, dnp=EXCLUDED.dnp,
                                 source=EXCLUDED.source, updated_at=now()""", rows)
        conn.commit()
        out["written"] = len(rows)
    return out


def backfill(conn, through_date, days: int, fetch_day=None) -> dict:
    """Walk back `days` nights from the day before `through_date`. Safe to re-run."""
    fetch = fetch_day or boxscores.fetch_day
    out = {"days": {}, "errors": {}}
    for i in range(1, days + 1):
        d = through_date - timedelta(days=i)
        try:
            out["days"][str(d)] = ingest_day(conn, d, fetch(d))
        except Exception as e:  # noqa: BLE001 - one bad night must not stop the walk
            out["errors"][str(d)] = f"{type(e).__name__}: {e}"[:200]
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_gamelogs_db.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dfs/gamelogs.py tests/test_gamelogs_db.py
git commit -m "Ingest verified ESPN box scores into per-player game logs"
```

---

### Task 3: Recent rates

**Files:**
- Modify: `dfs/gamelogs.py`
- Modify: `tests/test_gamelogs_db.py`

**Interfaces:**
- Consumes: `player_games` from Task 1.
- Produces: `gamelogs.recent_rates(conn, name_norms, before_date, lookback) -> {name_norm: {"rate": float, "minutes": float, "games": int, "last_game_date": date}}`. Keyed on `name_norm` because `prepare()` runs before players have database ids.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_gamelogs_db.py
def _log(conn, name, entries):
    from dfs.names import name_norm
    with conn.cursor() as cur:
        cur.execute("INSERT INTO players(name, name_norm) VALUES (%s,%s) RETURNING id",
                    (name, name_norm(name)))
        pid = cur.fetchone()["id"]
        for d, mins, dk in entries:
            cur.execute("""INSERT INTO player_games(player_id, game_date, min, dk_pts)
                           VALUES (%s,%s,%s,%s)""", (pid, d, mins, dk))
    conn.commit()
    return name_norm(name)


def test_recent_rate_is_dk_points_per_minute(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate One", [(datetime.date(2026, 10, 1), 30, 45),
                                     (datetime.date(2026, 10, 3), 20, 15)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 10), lookback=10)
    assert out[nn]["games"] == 2 and out[nn]["minutes"] == 50
    assert abs(out[nn]["rate"] - (60 / 50)) < 1e-9


def test_games_on_or_after_the_slate_date_are_excluded(monkeypatch):
    """A player's own evening must not leak into the projection meant to predict it."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate Two", [(datetime.date(2026, 10, 4), 30, 30),
                                     (datetime.date(2026, 10, 5), 30, 90)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 5), lookback=10)
    assert out[nn]["games"] == 1 and abs(out[nn]["rate"] - 1.0) < 1e-9


def test_lookback_takes_only_the_most_recent_games(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate Three", [(datetime.date(2026, 10, d), 10, 10) for d in range(1, 9)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 20), lookback=3)
    assert out[nn]["games"] == 3 and out[nn]["last_game_date"] == datetime.date(2026, 10, 8)


def test_zero_minute_games_do_not_count(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        nn = _log(conn, "Rate Four", [(datetime.date(2026, 10, 1), 0, 0),
                                      (datetime.date(2026, 10, 2), 25, 50)])
        out = gamelogs.recent_rates(conn, [nn], datetime.date(2026, 10, 10), lookback=10)
    assert out[nn]["games"] == 1 and abs(out[nn]["rate"] - 2.0) < 1e-9


def test_unknown_players_are_simply_absent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        out = gamelogs.recent_rates(conn, ["nobody at all"], datetime.date(2026, 10, 10), lookback=10)
    assert out == {}


def test_a_shared_normalised_name_is_not_merged(monkeypatch):
    """Two players normalising to the same name must not have their games pooled into one rate."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, gamelogs
    with db.connect() as conn:
        db.migrate(conn)
        _log(conn, "Dup Name", [(datetime.date(2026, 10, 1), 10, 60)])
        _log(conn, "Dup Name", [(datetime.date(2026, 10, 2), 10, 0)])
        out = gamelogs.recent_rates(conn, ["dup name"], datetime.date(2026, 10, 10), lookback=10)
    assert "dup name" not in out, "ambiguous names yield no rate rather than a merged one"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_gamelogs_db.py -q`
Expected: FAIL — `module 'dfs.gamelogs' has no attribute 'recent_rates'`.

- [ ] **Step 3: Write the implementation**

```python
# append to dfs/gamelogs.py
def recent_rates(conn, name_norms, before_date, lookback: int) -> dict:
    """DK points per minute over each player's last `lookback` games with minutes, strictly
    before `before_date`.

    Keyed on name_norm because prepare() runs before players have database ids. A name_norm
    shared by two players yields no rate at all: merging their games would corrupt both."""
    names = sorted({n for n in name_norms if n})
    if not names:
        return {}
    with conn.cursor() as cur:
        cur.execute("""SELECT p.name_norm, count(*) AS n_ids FROM players p
                       WHERE p.name_norm = ANY(%s) GROUP BY p.name_norm""", (names,))
        ambiguous = {r["name_norm"] for r in cur.fetchall() if r["n_ids"] > 1}
        usable = [n for n in names if n not in ambiguous]
        if not usable:
            return {}
        cur.execute("""SELECT name_norm, game_date, min, dk_pts FROM (
                         SELECT p.name_norm, g.game_date, g.min, g.dk_pts,
                                row_number() OVER (PARTITION BY p.name_norm
                                                   ORDER BY g.game_date DESC) AS rn
                         FROM player_games g JOIN players p ON p.id = g.player_id
                         WHERE p.name_norm = ANY(%s) AND g.game_date < %s AND g.min > 0
                       ) t WHERE rn <= %s""", (usable, before_date, lookback))
        rows = cur.fetchall()
    agg = {}
    for r in rows:
        a = agg.setdefault(r["name_norm"], {"minutes": 0.0, "points": 0.0, "games": 0,
                                            "last_game_date": None})
        a["minutes"] += float(r["min"])
        a["points"] += float(r["dk_pts"])
        a["games"] += 1
        if a["last_game_date"] is None or r["game_date"] > a["last_game_date"]:
            a["last_game_date"] = r["game_date"]
    return {k: {"rate": (v["points"] / v["minutes"]) if v["minutes"] else 0.0,
                "minutes": v["minutes"], "games": v["games"],
                "last_game_date": v["last_game_date"]}
            for k, v in agg.items()}
```

- [ ] **Step 4: Run to verify they pass**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_gamelogs_db.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dfs/gamelogs.py tests/test_gamelogs_db.py
git commit -m "Read recent DK points per minute from the game log"
```

---

### Task 4: The m_form signal

**Files:**
- Modify: `dfs/signals.py`
- Modify: `dfs/params.py`
- Create: `tests/test_form_signal.py`

**Interfaces:**
- Consumes: player keys `proj_rg`, `minutes`, `contingent`, and the rate fields Task 5 attaches: `recent_rate`, `recent_games`, `recent_minutes`, `recent_last_date`; plus `slate_date` on the player for the staleness check.
- Produces: `signals.form_multiplier(p: dict, sp: dict) -> float`; `"m_form"` appended to `SIGNAL_COLUMNS`; six new `signals` params.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_form_signal.py
"""The recent-form multiplier. Pure: no database, no clock, no network."""
import datetime

from dfs.params import default_params
from dfs.signals import form_multiplier

SP = default_params()["signals"]
SLATE = datetime.date(2026, 11, 6)


def mk(proj=32.0, minutes=32.0, rate=1.0, games=10, mins_total=300.0, last_days=1, **kw):
    return {"proj_rg": proj, "minutes": minutes, "recent_rate": rate, "recent_games": games,
            "recent_minutes": mins_total,
            "recent_last_date": SLATE - datetime.timedelta(days=last_days),
            "slate_date": SLATE, **kw}


def test_a_projection_inside_tolerance_is_untouched():
    """The common case must be a no-op."""
    p = mk(proj=33.0, minutes=32.0, rate=1.0)      # implied 1.031 vs rate 1.0 = +3.1%
    assert form_multiplier(p, SP) == 1.0


def test_a_projection_far_above_the_recent_rate_is_pulled_back():
    p = mk(proj=48.0, minutes=32.0, rate=1.0)      # implied 1.5 vs 1.0 = +50%
    m = form_multiplier(p, SP)
    assert m < 1.0
    assert abs(m - (1 + SP["form_tolerance"]) * 1.0 / 1.5) < 1e-9


def test_the_cut_is_capped():
    """One signal must never halve a player."""
    p = mk(proj=200.0, minutes=32.0, rate=1.0)
    assert form_multiplier(p, SP) == SP["form_max_cut"]


def test_a_projection_far_below_the_recent_rate_nudges_up_and_is_capped_tighter():
    p = mk(proj=8.0, minutes=32.0, rate=1.0)
    m = form_multiplier(p, SP)
    assert m == SP["form_max_boost"]
    assert SP["form_max_boost"] < 1 / SP["form_max_cut"], "the boost side is deliberately tighter"


def test_too_few_games_says_nothing():
    assert form_multiplier(mk(proj=60.0, games=SP["form_min_games"] - 1), SP) == 1.0


def test_too_few_minutes_says_nothing():
    """A freak 20-point, 8-minute night is not a rate."""
    assert form_multiplier(mk(proj=5.0, mins_total=SP["form_min_minutes"] - 1, rate=3.0), SP) == 1.0


def test_a_contingent_player_is_never_adjusted():
    """His role is changing because someone ahead of him is out, so the recent rate describes a
    job he no longer has. Without this the signal would punish exactly the cheap plays the pool
    rule exists to surface."""
    assert form_multiplier(mk(proj=60.0, rate=0.5, contingent=True), SP) == 1.0


def test_a_stale_last_game_says_nothing():
    """Back from injury: the recent rate describes a different player."""
    assert form_multiplier(mk(proj=60.0, last_days=20), SP) == 1.0


def test_zero_minutes_or_zero_rate_cannot_divide_by_zero():
    assert form_multiplier(mk(proj=40.0, minutes=0.0), SP) == 1.0
    assert form_multiplier(mk(proj=40.0, rate=0.0), SP) == 1.0


def test_missing_history_says_nothing():
    assert form_multiplier({"proj_rg": 40.0, "minutes": 30.0}, SP) == 1.0


def test_form_is_a_stored_signal_column():
    from dfs.signals import SIGNAL_COLUMNS
    assert "m_form" in SIGNAL_COLUMNS, "stored per build so grading can measure it"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_form_signal.py -q`
Expected: FAIL — `cannot import name 'form_multiplier'`.

- [ ] **Step 3: Add the parameters**

In `dfs/params.py`, inside the `"signals"` block:

```python
        # Recent form: how far a projection may sit above the player's own recent DK points per
        # minute before it is pulled back. Judgement, not measurement - there are no graded
        # slates behind these yet, so they are capped conservatively and are the learner's first
        # target. The boost side is tighter than the cut side because RotoGrinders already
        # chases hot play, so the upside is the side more likely to double-count.
        "form_lookback_games": 10,
        "form_tolerance": 0.15,
        "form_max_cut": 0.85,
        "form_max_boost": 1.08,
        "form_min_games": 4,
        "form_min_minutes": 60,
        "form_max_staleness_days": 14,
```

- [ ] **Step 4: Write the signal**

In `dfs/signals.py`, add `"m_form"` to `SIGNAL_COLUMNS`, add the function, and multiply it in `signal_components`:

```python
def form_multiplier(p: dict, sp: dict) -> float:
    """Pull a projection toward the player's recent DK points per minute when it requires an
    implausible jump. Returns 1.0 - no adjustment - whenever the signal has nothing to say."""
    if p.get("contingent"):
        return 1.0                      # role is changing; the recent rate describes another job
    rate = float(p.get("recent_rate") or 0)
    games = int(p.get("recent_games") or 0)
    rec_min = float(p.get("recent_minutes") or 0)
    minutes = float(p.get("minutes") or 0)
    proj = float(p.get("proj_rg") or 0)
    if rate <= 0 or minutes <= 0 or proj <= 0:
        return 1.0
    if games < sp["form_min_games"] or rec_min < sp["form_min_minutes"]:
        return 1.0
    last, slate = p.get("recent_last_date"), p.get("slate_date")
    if last and slate and (slate - last).days > sp["form_max_staleness_days"]:
        return 1.0                      # back from a long absence; the rate is a different player
    implied = proj / minutes
    jump = implied / rate - 1.0
    tol = sp["form_tolerance"]
    if jump > tol:
        return max(sp["form_max_cut"], (1 + tol) * rate / implied)
    if jump < -tol:
        return min(sp["form_max_boost"], (1 - tol) * rate / implied)
    return 1.0
```

In `signal_components`, after the contingent branch:

```python
    c["m_form"] = form_multiplier(p, sp)
```

And in `signal_multiplier`'s `fired` list, after the contingent check:

```python
    if c["m_form"] != 1.0:
        fired.append("form_cut" if c["m_form"] < 1.0 else "form_boost")
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_form_signal.py tests/test_core.py -q`
Expected: all pass. If `test_core.py` asserts an exact signal count or multiplier, update it to include the new component rather than weakening the new tests.

- [ ] **Step 6: Commit**

```bash
git add dfs/signals.py dfs/params.py tests/test_form_signal.py
git commit -m "Add the recent-form multiplier with its exclusions"
```

---

### Task 5: Wire rates into the build

**Files:**
- Modify: `dfs/pipeline.py`
- Create: `tests/test_form_pipeline_db.py`

**Interfaces:**
- Consumes: `gamelogs.recent_rates` (Task 3), `form_multiplier` (Task 4).
- Produces: `pipeline.prepare(dk_text, rg_text, params, lineup_src=None, rates=None)` — `rates` is `{name_norm: {...}}` as `recent_rates` returns. `run_build` and `run_refresh` fetch it. `_summary(prep)` gains `"form": {"scaled": int, "no_history": int}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_form_pipeline_db.py
"""The signal has to reach the optimizer, and an empty game log must change nothing."""
import datetime
import os

import pytest

from tests.test_pipeline import synthetic_files

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

NO_PAGES = lambda: {"rotowire": None, "rg": None, "errors": {}, "raw": {}}  # noqa: E731


def test_a_build_with_no_game_logs_is_unchanged_and_says_so(monkeypatch):
    """Review Focus 3: on the first run the table is empty. Every player gets 1.0."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import pipeline
    dk, rg = synthetic_files(seed=31, n_games=4)
    dk = dk.replace("11/02/2026", "02/02/2027")
    out = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)
    assert out["form"]["scaled"] == 0
    assert out["form"]["no_history"] > 0
    from dfs import db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pool WHERE slate_id=%s AND m_form <> 1", (out["slate_id"],))
        assert cur.fetchone()["n"] == 0


def test_a_logged_player_with_a_wild_projection_is_scaled_and_stored(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, pipeline
    from dfs.names import name_norm
    from dfs.sources.uploads import parse_dk_salaries
    dk, rg = synthetic_files(seed=32, n_games=4)
    dk = dk.replace("11/02/2026", "02/05/2027")
    victim = parse_dk_salaries(dk)["players"][0]["name"]

    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO players(name, name_norm) VALUES (%s,%s) RETURNING id",
                        (victim, name_norm(victim)))
            pid = cur.fetchone()["id"]
            for d in range(1, 9):   # a low, steady rate: 0.3 DK points per minute
                cur.execute("""INSERT INTO player_games(player_id, game_date, min, dk_pts)
                               VALUES (%s,%s,30,9)""", (pid, datetime.date(2027, 1, 20 + d)))
        conn.commit()

    out = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)
    assert out["form"]["scaled"] >= 1
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m_form FROM pool p JOIN players pl ON pl.id = p.player_id
                       WHERE p.slate_id=%s AND pl.name_norm=%s""", (out["slate_id"], name_norm(victim)))
        assert float(cur.fetchone()["m_form"]) < 1.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_form_pipeline_db.py -q`
Expected: FAIL — `KeyError: 'form'`.

- [ ] **Step 3: Write the implementation**

In `dfs/pipeline.py`, change `prepare`'s signature and attach the rates before `apply_signals`:

```python
def prepare(dk_text: str, rg_text: str, params: dict, lineup_src: dict | None = None,
            rates: dict | None = None) -> dict:
    dk = parse_dk_salaries(dk_text)
    rg = parse_rg_projections(rg_text)
    m = merge(dk, rg)
    players = m["players"]
    starts = {g["game"]: g["start"] for g in dk["games"]}
    for p in players:
        p["key"] = p["draftable_id"]
        p["game_start"] = starts[p["game"]].isoformat() if p.get("game") in starts else None
    lineup_report = None
    if lineup_src is not None:
        lineup_report = lineup_sources.apply_lineups(players, lineup_src)
        lineup_report["errors"] = lineup_src.get("errors", {})
    # recent form needs the slate date and must be attached before the signal layer runs
    for p in players:
        r = (rates or {}).get(p.get("name_norm")) or {}
        p["slate_date"] = dk["date"]
        p["recent_rate"] = r.get("rate")
        p["recent_games"] = r.get("games")
        p["recent_minutes"] = r.get("minutes")
        p["recent_last_date"] = r.get("last_game_date")
    apply_signals(players, params)
    estimate_missing_ownership(players)
    board = value_board(players, params)
    return {"dk": dk, "merge": m, "players": players, "value_board": board, "lineups": lineup_report}
```

Add a helper and use it in both callers:

```python
def _rates_for(conn, dk_text, params):
    """Recent rates keyed by name_norm, for the slate's own date."""
    from . import gamelogs
    from .sources.uploads import parse_dk_salaries as _dk
    dk = _dk(dk_text)
    names = [p["name_norm"] for p in dk["players"]]
    return gamelogs.recent_rates(conn, names, dk["date"], params["signals"]["form_lookback_games"])
```

In `run_build`, replace the `prepare` call:

```python
        rates = _rates_for(conn, dk_text, params)
        prep = prepare(dk_text, rg_text, params, lineup_src, rates)
```

In `run_refresh`, both `prepare` calls take the same `rates` (compute it once, just after `dk_text` is resolved).

In `_summary`, add the counts:

```python
    scaled = sum(1 for p in prep["players"] if (p.get("m_form") or 1.0) != 1.0)
    no_history = sum(1 for p in prep["players"] if not p.get("recent_games"))
```

and include `"form": {"scaled": scaled, "no_history": no_history}` in the returned dict.

- [ ] **Step 4: Run to verify they pass**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_form_pipeline_db.py -q`
Expected: all pass.

- [ ] **Step 5: Run the whole suite**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q`
Expected: all pass. `prepare` gained an optional argument, so existing callers are unaffected.

- [ ] **Step 6: Commit**

```bash
git add dfs/pipeline.py tests/test_form_pipeline_db.py
git commit -m "Attach recent rates to the pool so the form signal reaches the optimizer"
```

---

### Task 6: Scheduling, visibility and docs

**Files:**
- Modify: `dfs/jobs.py`
- Modify: `api/cron.py`
- Modify: `public/index.html`
- Modify: `PLAN.md`
- Modify: `tests/test_jobs_build.py`

**Interfaces:**
- Consumes: `gamelogs.ingest_day`, `gamelogs.backfill`.
- Produces: `jobs.backfill_logs(days=14) -> dict`; `api/cron.py` accepts `job=backfill`; `grade()` also ingests the same day's logs.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_jobs_build.py
def test_backfill_job_records_its_days(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr("dfs.gamelogs.backfill",
                        lambda conn, through_date, days, **kw: {"days": {"2026-11-05": {"written": 3}},
                                                                "errors": {}})
    out = jobs.backfill_logs(days=1)
    assert out["ok"] is True
    assert out["summary"]["days"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest tests/test_jobs_build.py -q`
Expected: FAIL — `module 'dfs.jobs' has no attribute 'backfill_logs'`.

- [ ] **Step 3: Add the job**

In `dfs/jobs.py`:

```python
def backfill_logs(days: int = 14, now: datetime | None = None) -> dict:
    """One-off seeding of player_games. Safe to re-run: ingest upserts."""
    from . import gamelogs
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        db.migrate(conn)
        rid = _start(conn, "backfill")
        errors, summary = {}, {}
        try:
            out = gamelogs.backfill(conn, history.et_date(now), days=days)
            summary["days"] = {d: v.get("written", 0) for d, v in out["days"].items()}
            errors.update(out["errors"])
        except Exception as e:  # noqa: BLE001
            errors["backfill"] = redact.scrub(f"{type(e).__name__}: {e}")[:300]
        ok = not errors
        _finish(conn, rid, ok, summary, errors)
    return {"ok": ok, "summary": summary, "errors": errors}
```

In `grade()`, inside the per-slate loop right after `days[d] = fetch_day(d)`, keep the log current with no new schedule:

```python
                    from . import gamelogs
                    try:
                        summary.setdefault("logged", {})[str(d)] = \
                            gamelogs.ingest_day(conn, d, days[d])["written"]
                    except Exception as e:  # noqa: BLE001 - logging must not fail grading
                        errors[f"gamelog_{d}"] = redact.scrub(f"{type(e).__name__}: {e}")[:200]
```

In `api/cron.py`, before the final `send`:

```python
    if job == "backfill":
        out = jobs.backfill_logs()
        return send(h, 500 if out["errors"] else 200, out)
```

- [ ] **Step 4: Show the gap in the UI**

In `public/index.html`, in the build-report notes:

```javascript
    if (j.form) notes.push(j.form.scaled
      ? `<span class="dim">Recent form scaled <b style="color:var(--chalk)">${j.form.scaled}</b> projection(s); ${j.form.no_history} player(s) had no game log yet.</span>`
      : `<span class="dim">Recent form changed nothing; ${j.form.no_history} player(s) had no game log yet.</span>`);
```

In `renderPool`, add a Form column after Proj:

```javascript
      <th class="n">Form</th>
```

and in the row, showing the required jump only when the signal fired:

```javascript
      <td class="n ${p.m_form && p.m_form < 1 ? "warn" : ""}">${p.m_form && p.m_form !== 1 ? ((p.m_form - 1) * 100).toFixed(0) + "%" : "–"}</td>
```

- [ ] **Step 5: Update `PLAN.md`**

Replace the `#8` line:

```markdown
- [x] #8 Recent-form check: DK points per minute over the last 10 games, from verified ESPN box
      scores in player_games. A projection more than 15% above that rate is pulled back, capped at
      -15%; below it nudges up, capped at +8%. Contingent players, short histories and players
      returning after 14+ days are excluded. ESPN was reachable all along; the cdn.nba.com note
      was the wrong door.
```

And under Binding rules, record the reversal:

```markdown
- Signals affect lineups as soon as they are built (owner's decision, 2026-11-06), rather than
  being shadow-logged until graded. The constants for an unmeasured signal are therefore capped
  conservatively and are the learner's first target.
```

- [ ] **Step 6: Run the whole suite**

Run: `TEST_DATABASE_URL="postgresql:///dfs_test" .venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add dfs/jobs.py api/cron.py public/index.html PLAN.md tests/test_jobs_build.py
git commit -m "Schedule game-log ingest, show the form gap, record the rule reversal"
```

---

## Deployment checklist

- [ ] Merge and let Vercel deploy.
- [ ] Trigger `job=backfill` once (Actions → nightly → Run workflow, or the cron endpoint) to seed ~14 nights.
- [ ] Rebuild a slate and confirm the report names how many projections were scaled.
- [ ] Check the pool table's Form column against two or three players by hand before trusting it.
