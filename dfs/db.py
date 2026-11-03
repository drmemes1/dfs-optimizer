"""Postgres access. DATABASE_URL is provided by the Vercel Neon integration."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
_MIGRATION_LOCK = 724_001  # pg_advisory_lock key so concurrent cold starts don't race


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


@contextmanager
def connect():
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=15) as conn:
        yield conn


def migrate(conn) -> list[str]:
    """Apply any migration files not yet recorded. Returns names applied this call."""
    applied_now = []
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                             name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
            cur.execute("SELECT name FROM schema_migrations")
            done = {r["name"] for r in cur.fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                cur.execute(path.read_text())
                cur.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (path.name,))
                applied_now.append(path.name)
            conn.commit()
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
            conn.commit()
    return applied_now


def seed_params(conn) -> int | None:
    """Insert the tuned starting params as version 1 if the table is empty."""
    from .params import default_params
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM params")
        if cur.fetchone()["n"]:
            return None
        cur.execute("INSERT INTO params(payload, reason, author) VALUES (%s, %s, 'human') RETURNING version",
                    (json.dumps(default_params()),
                     "Initial tuned values from graded backtests (see spec section 2c)"))
        v = cur.fetchone()["version"]
    conn.commit()
    return v


def current_params(conn) -> tuple[int, dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, payload FROM params ORDER BY version DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        raise RuntimeError("no params row; run migrate + seed_params")
    return r["version"], r["payload"]
