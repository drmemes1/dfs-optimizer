"""The 1pm build job. Every test stubs the stored-upload lookup explicitly so the shared session
database cannot decide the outcome."""
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

NOW = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)  # 1:00pm ET

LOBBY_MAIN = {"DraftGroups": [
    {"DraftGroupId": 156184, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
     "GameCount": 7}], "Contests": [{"dg": 156184, "po": 1_000_000}]}
LOBBY_DARK = {"DraftGroups": [
    {"DraftGroupId": 156191, "GameTypeId": 70, "StartDateEst": "2026-11-06T22:00:00.0000000",
     "GameCount": 2}], "Contests": [{"dg": 156191, "po": 400_000}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")


def test_no_qualifying_slate_pushes_a_low_priority_note_and_is_not_a_failure(monkeypatch):
    from dfs import jobs
    pushes = []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_DARK,
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is True
    assert out["summary"]["main_slate"] is None
    assert pushes and pushes[0]["priority"] == "low"
    assert "no main slate" in pushes[0]["title"].lower()
    assert "7-8pm et" in pushes[0]["body"].lower(), "the body should say why nothing qualified"


def test_missing_dk_upload_pushes_a_nudge_and_does_not_build(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: None)
    pushes, builds = [], []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     build_fn=lambda **kw: builds.append(kw),
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert builds == []
    assert out["ok"] is True
    assert out["summary"]["blocked"]
    assert pushes[0]["priority"] == "high"
    assert "dksalaries" in pushes[0]["body"].lower()


def test_builds_and_pushes_ready_when_the_upload_is_present(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv-text")
    monkeypatch.setattr(jobs, "_rg_names", lambda text: ["Devin Booker"])
    monkeypatch.setattr(jobs, "_dk_names", lambda text: ["Devin Booker"])
    pushes = []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=lambda **kw: {"slate_id": 7, "run_id": 3},
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is True, out["errors"]
    assert out["summary"]["slate_id"] == 7
    assert out["summary"]["rg_dk_overlap"] == 1.0
    assert any("ready" in p["title"].lower() for p in pushes)


def test_refuses_to_build_when_rotogrinders_returned_a_different_slate(monkeypatch):
    """The projections page is slate-scoped. Building from the wrong slate's numbers is the most
    dangerous failure in this design, so a low name overlap must stop the build."""
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv-text")
    monkeypatch.setattr(jobs, "_dk_names",
                        lambda text: ["Devin Booker", "Jalen Green", "Dillon Brooks"])
    monkeypatch.setattr(jobs, "_rg_names", lambda text: ["Nikola Jokic", "Jamal Murray"])
    pushes, builds = [], []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=lambda **kw: builds.append(kw),
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert builds == [], "must not build from another slate's projections"
    assert out["ok"] is False
    assert "slate_overlap" in out["errors"]
    assert pushes, "the refusal must reach the phone"
    assert "wrong slate" in pushes[0]["title"].lower()


def test_a_build_crash_is_pushed_not_swallowed(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv-text")
    monkeypatch.setattr(jobs, "_rg_names", lambda text: ["Devin Booker"])
    monkeypatch.setattr(jobs, "_dk_names", lambda text: ["Devin Booker"])
    pushes = []

    def boom(**kw):
        raise RuntimeError("optimizer infeasible")

    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=boom,
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is False
    assert "optimizer infeasible" in out["errors"]["build"]
    assert any("infeasible" in p["body"] for p in pushes)


def test_stored_dk_text_reads_the_upload_for_that_date(monkeypatch):
    """Exercises the real lookup, not a stub: it must find the newest DKSalaries bytes for a date."""
    from dfs import db, jobs
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO slates(date, draft_group_id, slate_key)
                           VALUES ('2027-05-04', 991001, 'Q@R') RETURNING id""")
            sid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO source_snapshots(slate_id, source, filename, raw, payload)
                           VALUES (%s,'dk_salaries_csv','DKSalaries.csv',%s,'{}')""",
                        (sid, b"Position,Name\nPG,Devin Booker\n"))
        conn.commit()
        got = jobs._stored_dk_text(conn, "2027-05-04")
    assert got is not None and "Devin Booker" in got
