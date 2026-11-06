"""Spec: "Credentials and session cookies are never logged, never returned by any endpoint, and
never written to job_runs summaries or errors. A test asserts this." """
import json
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

SENTINEL = "SENTINEL-PASSWORD-9f3a2b"
NOW = datetime(2027, 7, 1, 18, 0, tzinfo=timezone.utc)

LOBBY = {"DraftGroups": [
    {"DraftGroupId": 700001, "GameTypeId": 70, "StartDateEst": "2027-07-01T19:00:00.0000000",
     "GameCount": 7}], "Contests": [{"dg": 700001, "po": 1_000_000}]}


def test_a_credential_in_an_exception_never_reaches_job_runs(monkeypatch):
    """A careless library could put the password in an exception message. The job records
    errors verbatim, so the scrub has to happen at that boundary too."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("RG_PASS", SENTINEL)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    from dfs import db, jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv")
    monkeypatch.setattr(jobs, "_dk_game_count", lambda text: 7)

    def leaky():
        raise RuntimeError(f"POST /sign-in failed with password={SENTINEL}")

    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY, rg_fetch_fn=leaky,
                     push=lambda **kw: {"ok": False, "unconfigured": True, "error": "off"})
    assert out["ok"] is False
    assert SENTINEL not in json.dumps(out), "not in the value returned by /api/cron"

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT summary, errors FROM job_runs WHERE job='build'
                       ORDER BY started_at DESC LIMIT 1""")
        row = cur.fetchone()
    assert SENTINEL not in json.dumps(row["summary"]), "not in job_runs.summary"
    assert SENTINEL not in json.dumps(row["errors"]), "not in job_runs.errors"
