"""Each test owns a slate on its own date and asserts only about that slate: the session test
database is shared with every other test module, so other slates land in the same two-day window
jobs.prelock() scans, and global push counts would not be isolated."""
import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")


# 2027 dates, spaced 3 days apart. Other test modules build slates in Nov/Dec 2026
# (tests/test_contests.py uses 2026-12-01, tests/test_prefs.py 2026-12-05, and fixtures shift
# others), and jobs.prelock() scans a two-day window, so overlapping dates would let another
# module's slate alert inside our assertions.
def lock_at(day):
    """7:00pm ET on 2027-03-<day> == 00:00Z the next day (EST)."""
    return datetime(2027, 3, day + 1, 0, 0, tzinfo=timezone.utc)


def _make(monkeypatch, day, dg, key, lock=True):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("NTFY_TOPIC", "t")
    from dfs import db
    with db.connect() as conn:
        db.migrate(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO slates(date, draft_group_id, slate_key, lock_time_utc, n_games)
                           VALUES (%s,%s,%s,%s,1) RETURNING id""",
                        (f"2027-03-{day:02d}", dg, key, lock_at(day) if lock else None))
            sid = cur.fetchone()["id"]
        conn.commit()
    return sid, f"2027-03-{day:02d}"


def _mine(pushes, day_str, sid=None):
    """Pushes about our slate: the alert body starts with the date, the failure body names the
    slate id, and the date alone is unique to this test module."""
    out = [p for p in pushes if day_str in p["body"]]
    return [p for p in out if f"Slate {sid}" in p["body"]] if sid else out


def _alerted(out, sid):
    return [a for a in out["summary"]["alerted"] if a["slate_id"] == sid]


def test_alert_fires_once_then_is_suppressed(monkeypatch):
    sid, day = _make(monkeypatch, 2, 990101, "C@D")
    from dfs import jobs
    pushes = []
    monkeypatch.setattr("dfs.pipeline.run_refresh",
                        lambda **kw: {"slate_id": kw["slate_id"],
                                      "check": {"checked_lineups": 2, "flags": [], "checked_at": "x"}})
    kw = dict(rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
              push=lambda **k: pushes.append(k) or {"ok": True, "error": None})
    out1 = jobs.prelock(now=lock_at(2) - timedelta(minutes=18), **kw)
    out2 = jobs.prelock(now=lock_at(2) - timedelta(minutes=16), **kw)
    assert len(_mine(pushes, day)) == 1, "the alert must fire exactly once per slate"
    assert _alerted(out1, sid) and not _alerted(out2, sid)


def test_notifier_failure_does_not_fail_the_job_but_is_recorded(monkeypatch):
    sid, _ = _make(monkeypatch, 5, 990102, "E@F")
    from dfs import jobs
    monkeypatch.setattr("dfs.pipeline.run_refresh",
                        lambda **kw: {"slate_id": kw["slate_id"],
                                      "check": {"checked_lineups": 1, "flags": [], "checked_at": "x"}})
    out = jobs.prelock(now=lock_at(5) - timedelta(minutes=18),
                       rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                       push=lambda **kw: {"ok": False, "error": "connection refused"})
    assert _alerted(out, sid), "the sweep's real work still happened"
    assert "connection refused" in out["errors"][f"push_{sid}"]


def test_sweep_failure_pushes_the_error_once_not_every_tick(monkeypatch):
    sid, day = _make(monkeypatch, 8, 990103, "G@H")
    from dfs import jobs
    pushes = []

    def boom():
        raise RuntimeError("rotogrinders unreachable")

    kw = dict(rg_fetch_fn=boom, push=lambda **k: pushes.append(k) or {"ok": True, "error": None})
    out = jobs.prelock(now=lock_at(8) - timedelta(minutes=18), **kw)
    jobs.prelock(now=lock_at(8) - timedelta(minutes=13), **kw)
    mine = _mine(pushes, day, sid)
    assert "unreachable" in out["errors"][f"prelock_{sid}"]
    assert len(mine) == 1, "a failed sweep reports once, not every five minutes"
    assert "unreachable" in mine[0]["body"]


def test_no_alert_when_lock_time_is_null(monkeypatch):
    sid, day = _make(monkeypatch, 11, 990104, "I@J", lock=False)
    from dfs import jobs
    pushes = []
    out = jobs.prelock(now=lock_at(11) - timedelta(minutes=18),
                       rg_fetch_fn=lambda: ("x", b""),
                       push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert _mine(pushes, day, sid) == [], "a slate with no lock time must never alert"
    assert any(s["slate_id"] == sid for s in out["summary"]["skipped"])
    assert f"prelock_{sid}" not in out["errors"]


def test_a_repeated_failure_is_not_reported_as_new(monkeypatch):
    """cron.py turns new_errors into the HTTP status, so a source that is down all evening must
    fail the workflow once rather than emailing on all ~30 ticks."""
    sid, _ = _make(monkeypatch, 14, 990105, "K@L")
    from dfs import jobs

    def boom():
        raise RuntimeError("still down")

    kw = dict(rg_fetch_fn=boom, push=lambda **k: {"ok": True, "error": None})
    first = jobs.prelock(now=lock_at(14) - timedelta(minutes=18), **kw)
    second = jobs.prelock(now=lock_at(14) - timedelta(minutes=13), **kw)
    assert f"prelock_{sid}" in first["new_errors"], "the first failure is new; fail the workflow"
    assert f"prelock_{sid}" in second["errors"], "still recorded on the run"
    assert f"prelock_{sid}" not in second["new_errors"], "no longer new, so the workflow stays green"


def test_a_failed_send_does_not_consume_the_one_shot_alert(monkeypatch):
    """C2: a 30-second notifier outage at T-18 must not silence T-13, T-8 and T-3. The lead is
    20 minutes and ticks are 5 apart, so retries are free."""
    sid, day = _make(monkeypatch, 17, 990106, "M@N")
    from dfs import jobs
    monkeypatch.setattr("dfs.pipeline.run_refresh",
                        lambda **kw: {"slate_id": kw["slate_id"],
                                      "check": {"checked_lineups": 1, "flags": [], "checked_at": "x"}})
    sends = []

    def flaky(**kw):
        sends.append(kw)
        ok = len(sends) > 1           # first attempt fails, second succeeds
        return {"ok": ok, "error": None if ok else "resend 502", "unconfigured": False}

    kw = dict(rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""), push=flaky)
    jobs.prelock(now=lock_at(17) - timedelta(minutes=18), **kw)
    out2 = jobs.prelock(now=lock_at(17) - timedelta(minutes=13), **kw)
    assert len(_mine(sends, day)) == 2, "the next tick must retry after a failed send"
    assert _alerted(out2, sid), "and the retry must actually alert"


def test_alerts_switched_off_is_not_reported_as_an_error(monkeypatch):
    """I3/Focus 5: with no transport configured the sweep still does its work, and 'off' must not
    be recorded as a failure or it fails the workflow on every successful sweep."""
    sid, _ = _make(monkeypatch, 20, 990107, "O@P")
    from dfs import jobs
    monkeypatch.setattr("dfs.pipeline.run_refresh",
                        lambda **kw: {"slate_id": kw["slate_id"],
                                      "check": {"checked_lineups": 1, "flags": [], "checked_at": "x"}})
    out = jobs.prelock(now=lock_at(20) - timedelta(minutes=18),
                       rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                       push=lambda **kw: {"ok": False, "unconfigured": True, "error": "no transport"})
    assert _alerted(out, sid), "the sweep's real work still happened"
    assert f"push_{sid}" not in out["errors"], "switched off is not a failure"
    assert out["new_errors"] == {}


def test_a_degraded_sweep_says_so_in_the_alert(monkeypatch):
    """If the fresh RotoGrinders read was rejected as another slate's, run_refresh falls back to
    the stored projections. The alert must say the numbers are not fresh."""
    sid, _ = _make(monkeypatch, 23, 990108, "Q@R")
    from dfs import jobs
    monkeypatch.setattr("dfs.pipeline.run_refresh",
                        lambda **kw: {"slate_id": kw["slate_id"],
                                      "degraded": "the fresh RotoGrinders read matched only 11%",
                                      "check": {"checked_lineups": 1, "flags": [], "checked_at": "x"}})
    pushes = []
    out = jobs.prelock(now=lock_at(23) - timedelta(minutes=18),
                       rg_fetch_fn=lambda: ("#RG-TABLE\tx\tx\t1", b""),
                       push=lambda **kw: pushes.append(kw) or {"ok": True, "error": None})
    assert [a for a in out["summary"]["alerted"] if a["slate_id"] == sid][0]["degraded"]
    assert any("11%" in p["body"] for p in pushes), "the user must know the projections are stale"
