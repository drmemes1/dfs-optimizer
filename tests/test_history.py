import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dfs.sources.lineups import parse_rg_lineups, parse_rotowire

FX = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")


def _src():
    return {"rotowire": parse_rotowire((FX / "rotowire_sample.html").read_text()),
            "rg": parse_rg_lineups((FX / "rg_lineups_sample.html").read_text()), "errors": {}, "raw": {}}


def test_history_records_only_changes(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, history
    t1 = datetime(2031, 1, 5, 20, 0, tzinfo=timezone.utc)
    t2 = datetime(2031, 1, 5, 23, 0, tzinfo=timezone.utc)
    with db.connect() as conn:
        db.migrate(conn)
        first = history.record(conn, _src(), t1)
        assert first["player_changes"] > 10 and first["vegas_changes"] == 2
        again = history.record(conn, _src(), t2)
        assert again == {"player_changes": 0, "vegas_changes": 0, "game_date": "2031-01-05"}
        src = _src()
        src["rotowire"][0]["teams"]["CLE"]["starters"][1]["inj"] = "O"      # Mitchell ruled out
        src["rotowire"][0]["total"] = 231.5                                 # line moved
        changed = history.record(conn, src, t2)
        assert changed["player_changes"] == 1 and changed["vegas_changes"] == 1
        with conn.cursor() as cur:
            cur.execute("""SELECT inj, observed_at FROM player_status_history WHERE game_date='2031-01-05'
                           AND name='Donovan Mitchell' AND source='rotowire' ORDER BY observed_at""")
            rows = cur.fetchall()
        assert [r["inj"] for r in rows] == ["GTD", "O"]                    # the sequence is kept


def test_snapshot_job_runs_and_logs(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, jobs
    out = jobs.snapshot(now=datetime(2031, 2, 1, 22, 0, tzinfo=timezone.utc), fetch=_src)
    assert out["ok"] and out["summary"]["history"]["player_changes"] > 0
    with db.connect() as conn:
        runs = jobs.recent_runs(conn, 1)
    assert runs[0]["job"] == "snapshot" and runs[0]["ok"] is True
    bad = jobs.snapshot(now=datetime(2031, 2, 1, 22, 20, tzinfo=timezone.utc),
                        fetch=lambda: {"rotowire": None, "rg": None, "errors": {"rotowire": "HTTP 503"}, "raw": {}})
    assert not bad["ok"] and "rotowire" in bad["errors"]               # failures are recorded, not swallowed
