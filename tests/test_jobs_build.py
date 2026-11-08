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
    monkeypatch.setattr(jobs, "_dk_game_count", lambda text: 7)
    pushes = []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=lambda **kw: {"slate_id": 7, "run_id": 3, "dk_coverage": 0.97},
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is True, out["errors"]
    assert out["summary"]["slate_id"] == 7
    assert out["summary"]["dk_coverage"] == 0.97, "telemetry for tuning the threshold"
    assert any("ready" in p["title"].lower() for p in pushes)


def test_refuses_to_build_when_rotogrinders_returned_a_different_slate(monkeypatch):
    """The projections page is slate-scoped. pipeline.run_build raises SlateMismatch; the job's
    duty is to surface it rather than let a wrecked slate through."""
    from dfs import jobs, pipeline
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv-text")
    monkeypatch.setattr(jobs, "_dk_game_count", lambda text: 7)
    pushes = []

    def refuse(**kw):
        raise pipeline.SlateMismatch("matches only 12% of this DraftKings slate; refusing to "
                                     "build from what looks like a different slate")

    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=refuse,
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert out["ok"] is False
    assert "different slate" in out["errors"]["build"]
    assert pushes, "the refusal must reach the user"
    assert "refused" in pushes[0]["title"].lower() or "failed" in pushes[0]["title"].lower()


def test_refuses_when_the_uploaded_file_is_not_the_slate_we_picked(monkeypatch):
    """I2: the game count in the push comes from the lobby and the lineups come from the file.
    Uploading the late 2-game export would otherwise build it and announce '7-game slate'."""
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv-text")
    monkeypatch.setattr(jobs, "_dk_game_count", lambda text: 2)      # lobby says 7
    pushes, builds = [], []
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                     build_fn=lambda **kw: builds.append(kw),
                     push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert builds == [], "must not build from a different slate's salary file"
    assert out["ok"] is False
    assert "dk_file_mismatch" in out["errors"]
    assert "2" in pushes[0]["body"] and "7" in pushes[0]["body"], "say both counts"


def test_a_build_crash_is_pushed_not_swallowed(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: "dk-csv-text")
    monkeypatch.setattr(jobs, "_dk_game_count", lambda text: 7)
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


def test_an_undeliverable_nudge_is_recorded_so_the_run_is_not_silently_green(monkeypatch):
    """I3: spec criterion 4 is 'no run can fail silently'. A nudge that went nowhere means the
    user is never told to upload, and at 7pm there are no lineups and no slate to alert on."""
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: None)
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     push=lambda **kw: {"ok": False, "unconfigured": False, "error": "resend 500"})
    assert out["ok"] is False
    assert any("resend 500" in str(v) for v in out["errors"].values())


def test_alerts_switched_off_leaves_the_nudge_run_green(monkeypatch):
    """...but deliberately having no transport configured is not a failure."""
    from dfs import jobs
    monkeypatch.setattr(jobs, "_stored_dk_text", lambda conn, d: None)
    out = jobs.build(now=NOW, lobby_fn=lambda: LOBBY_MAIN,
                     push=lambda **kw: {"ok": False, "unconfigured": True, "error": "no transport"})
    assert out["ok"] is True
    assert out["errors"] == {}


def test_backfill_job_records_its_days(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr("dfs.gamelogs.backfill",
                        lambda conn, through_date, days, **kw: {"days": {"2026-11-05": {"written": 3}},
                                                                "errors": {}})
    out = jobs.backfill_logs(days=1)
    assert out["ok"] is True
    assert out["summary"]["days"] == {"2026-11-05": 3}


def test_backfill_job_reports_a_bad_night(monkeypatch):
    from dfs import jobs
    monkeypatch.setattr("dfs.gamelogs.backfill",
                        lambda conn, through_date, days, **kw: {"days": {},
                                                                "errors": {"2026-11-04": "espn down"}})
    out = jobs.backfill_logs(days=1)
    assert out["ok"] is False
    assert "2026-11-04" in out["errors"]


def test_backfill_job_surfaces_names_it_could_not_place(monkeypatch):
    """A name the matcher cannot place writes no game log at all, so that player silently never
    gets a form adjustment. The count has to be visible."""
    from dfs import jobs
    monkeypatch.setattr("dfs.gamelogs.backfill",
                        lambda conn, through_date, days, **kw: {
                            "days": {"2026-11-05": {"written": 3, "unmatched": ["Nobody Known"],
                                                    "ambiguous": [], "team_mismatch": []}},
                            "errors": {}})
    out = jobs.backfill_logs(days=1)
    assert out["summary"]["unplaced"]["unmatched"]["count"] == 1
    assert "Nobody Known" in out["summary"]["unplaced"]["unmatched"]["sample"]
