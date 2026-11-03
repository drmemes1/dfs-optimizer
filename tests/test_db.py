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
