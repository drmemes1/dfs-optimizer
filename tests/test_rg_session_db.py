"""The durable half of the login backoff. The unit tests drive session_cookie through a hand-written
fake cursor, which pins the control flow but verifies nothing about the real SQL."""
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

NOW = datetime(2027, 8, 1, 12, 0, tzinfo=timezone.utc)


def test_failures_accumulate_and_a_stored_session_resets_them(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    from dfs.sources import rg_fetch
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rg_session")
        conn.commit()

        for _ in range(rg_fetch.MAX_LOGIN_FAILURES):
            rg_fetch._record_failure(conn, NOW)
        row = rg_fetch._read_session(conn)
        assert row["failures"] == rg_fetch.MAX_LOGIN_FAILURES
        assert row["cookie"] is None
        assert rg_fetch._cooling_off(row, NOW) is True, "the cap must trip on real rows"

        rg_fetch._store_session(conn, "rguid=good", NOW)
        row = rg_fetch._read_session(conn)
        assert row["cookie"] == "rguid=good"
        assert row["failures"] == 0, "a working login clears the cooldown"
        assert rg_fetch._cooling_off(row, NOW) is False

        rg_fetch.clear_session(conn)
        assert rg_fetch._read_session(conn)["cookie"] is None

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM rg_session")
            assert cur.fetchone()["n"] == 1, "rg_session holds exactly one row"
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rg_session")
        conn.commit()
