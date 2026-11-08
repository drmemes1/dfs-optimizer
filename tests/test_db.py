"""Runs against a local Postgres when TEST_DATABASE_URL is set; skipped otherwise."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")


def test_migrate_seed_idempotent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    with db.connect() as conn:
        db.migrate(conn)
        assert db.migrate(conn) == []
        db.seed_params(conn)
        assert db.seed_params(conn) is None
        v, payload = db.current_params(conn)
        assert payload["GPP"]["own_penalty"] == 0.12


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


def test_current_params_fills_in_keys_added_after_the_row_was_written(monkeypatch):
    """A params row written before a new signal existed must not crash the build. Stored values
    still win; only missing keys fall back to the defaults."""
    import json
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    from dfs.params import default_params
    stale = default_params()
    del stale["signals"]["form_lookback_games"]
    stale["signals"]["pace_exponent"] = 0.42
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO params(payload, reason, author, kind)
                           VALUES (%s,'stale row','test','initial')""", (json.dumps(stale),))
        conn.commit()
        _, payload = db.current_params(conn)
    assert payload["signals"]["form_lookback_games"] == default_params()["signals"]["form_lookback_games"]
    assert payload["signals"]["pace_exponent"] == 0.42, "the stored value still wins"


def test_a_filled_params_row_is_appended_so_the_lineage_stays_honest(monkeypatch):
    """A params row is a snapshot of the keys that existed when it was written. Filling the gaps
    only in memory left two problems: paramstore reads the raw stored row, so the learner could
    never tune a newly added key; and params_version no longer identified what a build actually
    used. Appending the filled payload once fixes both."""
    import json
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    from dfs.params import default_params
    stale = default_params()
    del stale["signals"]["form_tolerance"]
    del stale["signals"]["form_max_cut"]
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO params(payload, reason, author, kind)
                           VALUES (%s,'stale row','test','initial') RETURNING version""",
                        (json.dumps(stale),))
            stale_version = cur.fetchone()["version"]
        conn.commit()

        version, payload = db.current_params(conn)
        assert version > stale_version, "a new row records the fill"
        assert payload["signals"]["form_tolerance"] == default_params()["signals"]["form_tolerance"]

        with conn.cursor() as cur:
            cur.execute("SELECT payload, kind, reason FROM params WHERE version=%s", (version,))
            row = cur.fetchone()
        assert "form_tolerance" in row["payload"]["signals"], "the stored row now has the key"
        assert row["kind"] == "migration"
        assert "form_tolerance" in row["reason"], "the reason names what was added"

        again_version, _ = db.current_params(conn)
        assert again_version == version, "nothing is appended when there is nothing to fill"


def test_filling_never_overwrites_a_stored_value(monkeypatch):
    import json
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    from dfs.params import default_params
    tuned = default_params()
    tuned["signals"]["pace_exponent"] = 0.37
    del tuned["signals"]["form_min_games"]
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO params(payload, reason, author, kind)
                           VALUES (%s,'tuned','test','measured_bias')""", (json.dumps(tuned),))
        conn.commit()
        _, payload = db.current_params(conn)
    assert payload["signals"]["pace_exponent"] == 0.37
    assert payload["signals"]["form_min_games"] == default_params()["signals"]["form_min_games"]
