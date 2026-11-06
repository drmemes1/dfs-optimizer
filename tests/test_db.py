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
