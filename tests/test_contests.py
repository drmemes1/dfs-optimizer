import csv
import io
import os

import pytest

from dfs.contests import clean_context
from dfs.sources.contest_results import parse_lineup, parse_standings
from dfs.sources.uploads import UploadError
from tests.test_pipeline import synthetic_files


def test_parse_lineup_cell_with_tricky_names():
    cell = ("C Jarrett Allen F C.J. McCollum G P.J. Washington PF Kelly Oubre Jr. PG Shai Gilgeous-Alexander "
            "SF G League Guy SG Day'Ron Sharpe UTIL Nikola Jokić")
    lu = parse_lineup(cell)
    assert [x["slot"] for x in lu] == ["C", "F", "G", "PF", "PG", "SF", "SG", "UTIL"]
    assert lu[1]["name"] == "C.J. McCollum" and lu[5]["name"] == "G League Guy" and lu[7]["name"] == "Nikola Jokić"


def test_standings_refuses_other_files():
    with pytest.raises(UploadError, match="not a DraftKings contest standings file"):
        parse_standings("name,salary\nx,1\n")


def test_contest_context_validation():
    assert clean_context({"name": "NBA $20K Shot", "entry_fee": "5", "field_size": "4756", "entry_mode": "ME",
                          "max_entries": "20", "top_heaviness": "top_heavy"})["entry_fee"] == 5.0
    with pytest.raises(UploadError):
        clean_context({"entry_mode": "XX"})


def _standings_csv(lineups, others, ownership):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup", "", "Player",
                "Roster Position", "%Drafted", "FPTS"])
    entries = []
    for i, (name, players, pts) in enumerate(lineups + others):
        entries.append((name, " ".join(f'{x["slot"]} {x["name"]}' for x in players), pts))
    entries.sort(key=lambda e: -e[2])
    own = list(ownership)
    for i, (name, cell, pts) in enumerate(entries):
        right = own[i] if i < len(own) else None
        w.writerow([i + 1, 1000 + i, name, 0, pts, cell, "",
                    *(right if right else ["", "", "", ""])])
    for right in own[len(entries):]:
        w.writerow(["", "", "", "", "", "", "", *right])
    return out.getvalue().encode()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")
def test_import_marks_my_entries_and_links_saved_lineups(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import contests, db, pipeline
    dk, rg = synthetic_files(seed=21, n_games=4)
    dk = dk.replace("11/02/2026", "12/01/2026")
    off = lambda: {"rotowire": None, "rg": None, "errors": {}, "raw": {}}  # noqa: E731
    built = pipeline.run_build(dk, rg, n_gpp=3, n_cash=1, fetch=off,
                               contest={"name": "Test GPP", "entry_fee": 5, "entry_mode": "ME", "max_entries": 3})
    sid = built["slate_id"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, players, contest_context FROM lineups WHERE slate_id=%s AND mode='GPP' ORDER BY rank", (sid,))
        gpp = cur.fetchall()
        cur.execute("SELECT p.name FROM pool pl JOIN players p ON p.id=pl.player_id WHERE pl.slate_id=%s", (sid,))
        names = [r["name"] for r in cur.fetchall()]
    assert gpp[0]["contest_context"]["name"] == "Test GPP"
    mine = [("drmemes1 (1/3)", gpp[0]["players"], 301.5), ("drmemes1 (2/3)", gpp[1]["players"], 250.0)]
    others = [(f"rival{i}", gpp[2]["players"], 200.0 + i) for i in range(20)]
    ownership = [[n, "UTIL", f"{(i % 40) + 0.5}%", 10 + i] for i, n in enumerate(names)]
    raw = _standings_csv(mine, others, ownership)
    meta = {"name": "Test GPP", "entry_fee": 5, "prize_pool": 100, "places_paid": 5, "dk_username": "drmemes1"}
    with db.connect() as conn:
        db.migrate(conn)
        res = contests.import_standings(conn, raw, "contest-standings-987654321.csv", meta)
        assert res["contest_id"] == 987654321 and res["slate_id"] == sid
        assert res["entries"] == 22 and res["mine"] == 2 and res["matched_to_saved_lineups"] == 2
        assert res["winning_score"] == 301.5 and res["min_cash_score"] == 217.0
        again = contests.import_standings(conn, raw, "contest-standings-987654321.csv", meta)   # idempotent
        assert again["entries"] == 22
        with conn.cursor() as cur:
            cur.execute("SELECT entered, actual_score, contest_result FROM lineups WHERE id=%s", (gpp[0]["id"],))
            r = cur.fetchone()
            assert r["entered"] and float(r["actual_score"]) == 301.5 and r["contest_result"]["rank"] == 1
            cur.execute("SELECT count(*) AS n, count(player_id) AS linked FROM contest_ownership WHERE contest_id=987654321")
            o = cur.fetchone()
            assert o["n"] == len(names) and o["linked"] == len(names)
            cur.execute("SELECT count(*) AS n FROM contest_entries WHERE contest_id=987654321")
            assert cur.fetchone()["n"] == 22
