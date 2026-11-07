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
    from dfs import db, pipeline
    dk, rg = synthetic_files(seed=31, n_games=4)
    dk = dk.replace("11/02/2026", "02/02/2027")
    out = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)
    assert out["form"]["scaled"] == 0
    assert out["form"]["no_history"] > 0
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pool WHERE slate_id=%s AND m_form <> 1",
                    (out["slate_id"],))
        assert cur.fetchone()["n"] == 0


def test_a_logged_player_with_a_wild_projection_is_scaled_and_stored(monkeypatch):
    """Mirrors reality: the player already exists from an earlier build, then game logs arrive,
    then the slate is rebuilt. Inserting a second players row with the same normalised name would
    instead trip the ambiguity guard and yield no rate at all."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, pipeline
    from dfs.names import name_norm
    from dfs.sources.uploads import parse_dk_salaries
    dk, rg = synthetic_files(seed=32, n_games=4)
    dk = dk.replace("11/02/2026", "02/05/2027")
    victim = parse_dk_salaries(dk)["players"][0]["name"]

    first = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)
    assert first["form"]["scaled"] == 0, "no logs yet, so nothing is adjusted"

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM players WHERE name_norm=%s", (name_norm(victim),))
            rows = cur.fetchall()
            assert len(rows) == 1, "the build created exactly one row for this player"
            pid = rows[0]["id"]
            for d in range(1, 9):   # a low, steady rate: 0.3 DK points per minute
                cur.execute("""INSERT INTO player_games(player_id, game_date, min, dk_pts)
                               VALUES (%s,%s,30,9) ON CONFLICT DO NOTHING""",
                            (pid, datetime.date(2027, 1, 20 + d)))
        conn.commit()

    out = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)
    assert out["form"]["scaled"] >= 1, "the logged player's projection is now pulled back"
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m_form FROM pool p JOIN players pl ON pl.id = p.player_id
                       WHERE p.slate_id=%s AND pl.name_norm=%s""",
                    (out["slate_id"], name_norm(victim)))
        assert float(cur.fetchone()["m_form"]) < 1.0


def test_the_form_multiplier_reaches_the_api_payload(monkeypatch):
    """The pool query lists its columns explicitly, so a new signal column is invisible to the
    page until it is added there."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, queries
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM slates ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        assert row, "an earlier test in this module built a slate"
        detail = queries.slate_detail(conn, row["id"])
    assert detail["pool"], "the slate has a pool"
    assert "m_form" in detail["pool"][0], "the page cannot show what the query does not select"
