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
        cur.execute("INSERT INTO params(payload, reason, author, kind) VALUES (%s, %s, 'human', 'initial') "
                    "RETURNING version",
                    (json.dumps(default_params()),
                     "Initial tuned values from graded backtests (see spec section 2c)"))
        v = cur.fetchone()["version"]
    conn.commit()
    return v


def _fill_defaults(stored: dict) -> dict:
    """Stored params over the current defaults, one level into each section.

    A params row is a snapshot of the keys that existed when it was written. Adding a signal adds
    a key the stored rows do not have, and reading it would raise KeyError on every build against
    an older row - a failure that cannot show up in tests, because a fresh database seeds its
    params from today's defaults. Filling the gaps here means a new key works everywhere from the
    moment it is added, and the stored value always wins where there is one."""
    from .params import default_params
    out = default_params()
    for section, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(out.get(section), dict):
            out[section] = {**out[section], **value}
        else:
            out[section] = value
    return out


def _missing_keys(stored: dict, filled: dict) -> list[str]:
    out = []
    for section, value in filled.items():
        if isinstance(value, dict):
            for k in value:
                if k not in (stored.get(section) or {}):
                    out.append(f"{section}.{k}")
        elif section not in stored:
            out.append(section)
    return sorted(out)


def current_params(conn) -> tuple[int, dict]:
    """The params in force, with any key added since the latest row was written filled in.

    When a fill actually happens the filled payload is appended as a new row, for two reasons.
    paramstore.apply_changes reads the raw stored payload and refuses keys it does not find, so
    without this the learner could never tune a newly added signal. And params_version is stamped
    on every pool and lineup, so if the effective value came from DEFAULT_PARAMS rather than the
    row, replaying a slate at that version would not reproduce what was built - and would drift
    every time the defaults were edited."""
    with conn.cursor() as cur:
        cur.execute("SELECT version, payload FROM params ORDER BY version DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        raise RuntimeError("no params row; run migrate + seed_params")
    filled = _fill_defaults(r["payload"])
    added = _missing_keys(r["payload"] or {}, filled)
    if not added:
        return r["version"], filled
    with conn.cursor() as cur:
        # re-read inside the write so two cold starts racing cannot both append
        cur.execute("SELECT version, payload FROM params ORDER BY version DESC LIMIT 1 FOR UPDATE")
        latest = cur.fetchone()
        if not _missing_keys(latest["payload"] or {}, _fill_defaults(latest["payload"])):
            conn.commit()
            return latest["version"], _fill_defaults(latest["payload"])
        cur.execute("""INSERT INTO params(payload, reason, author, kind)
                       VALUES (%s,%s,'system','migration') RETURNING version""",
                    (json.dumps(filled),
                     "filled keys added since v%s: %s" % (latest["version"], ", ".join(added))))
        version = cur.fetchone()["version"]
    conn.commit()
    return version, filled
