import os
import random

import pytest

from dfs.sources.boxscores import parse_summary, verify_game


def _espn_summary(team_rows):
    """team_rows: {abbr: [(name, min, pts, reb, ast, stl, blk, tov, fg3m, dnp)]}"""
    players = []
    for abbr, rows in team_rows.items():
        athletes = []
        for name, mn, pts, reb, ast, stl, blk, tov, fg3m, dnp in rows:
            athletes.append({"athlete": {"displayName": name}, "starter": False, "didNotPlay": dnp,
                             "stats": [] if dnp else [str(mn), f"{pts // 2}-{pts}", f"{fg3m}-{fg3m + 2}", "0-0",
                                                     "0", "0", str(reb), str(ast), str(stl), str(blk), str(tov), "0", "0", str(pts)]})
        players.append({"team": {"abbreviation": abbr}, "statistics": [{
            "labels": ["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
            "athletes": athletes}]})
    return {"boxscore": {"players": players}}


def _team(prefix, total_min=240, total_pts=110):
    rows, mins, pts = [], [36, 34, 33, 32, 30, 25, 20, 18, 12], [28, 22, 18, 14, 10, 8, 6, 3, 1]
    scale_m = total_min / sum(mins)
    m = [round(x * scale_m) for x in mins]
    m[0] += total_min - sum(m)
    p = list(pts)
    p[0] += total_pts - sum(p)
    for i in range(9):
        rows.append((f"{prefix} Player{i}", m[i], p[i], 5, 3, 1, 0, 2, 1, False))
    rows.append((f"{prefix} Bench", 0, 0, 0, 0, 0, 0, 0, 0, True))
    return rows


def _game(a_score=110, h_score=104, period=4, final=True):
    return {"event_id": "1", "final": final, "period": period,
            "teams": {"AAA": {"score": a_score, "home": False}, "HHH": {"score": h_score, "home": True}}}


def test_parse_summary_dk_points():
    box = parse_summary(_espn_summary({"AAA": _team("A")}))
    p0 = box["AAA"][0]
    assert p0["min"] > 0 and p0["fg3m"] == 1 and p0["dk_pts"] > p0["pts"]
    assert box["AAA"][-1]["dnp"] and box["AAA"][-1]["dk_pts"] == 0


def test_verification_guard():
    good = parse_summary(_espn_summary({"AAA": _team("A", 240, 110), "HHH": _team("H", 240, 104)}))
    bref = {frozenset({"AAA", "HHH"}): {"AAA": 110, "HHH": 104}}
    assert verify_game(_game(), good, bref) == []
    # frozen mid-game snapshot labeled final: half the minutes, half the points
    half = parse_summary(_espn_summary({"AAA": _team("A", 120, 55), "HHH": _team("H", 120, 52)}))
    probs = verify_game(_game(), half, bref)
    assert any("minutes sum to 120" in x for x in probs) and any("points sum to 55" in x for x in probs)
    # independent source disagrees
    assert any("disagrees" in x for x in verify_game(_game(), good, {frozenset({"AAA", "HHH"}): {"AAA": 112, "HHH": 104}}))
    # no independent score yet -> not accepted
    assert any("independent" in x for x in verify_game(_game(), good, {}))
    # overtime: 265 expected
    ot = parse_summary(_espn_summary({"AAA": _team("A", 265, 120), "HHH": _team("H", 265, 118)}))
    assert verify_game(_game(120, 118, period=5), ot, {frozenset({"AAA", "HHH"}): {"AAA": 120, "HHH": 118}}) == []
    assert verify_game(_game(final=False), good, bref) == ["ESPN does not mark the game final"]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")
def test_grade_slate_end_to_end(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, grading, pipeline
    from tests.test_pipeline import synthetic_files
    dk, rg = synthetic_files(seed=41, n_games=2)
    dk = dk.replace("11/02/2026", "12/10/2026")
    off = lambda: {"rotowire": None, "rg": None, "errors": {}, "raw": {}}  # noqa: E731
    built = pipeline.run_build(dk, rg, n_gpp=3, n_cash=1, fetch=off)
    sid = built["slate_id"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT p.name, pl.team, pl.proj_adj FROM pool pl JOIN players p ON p.id=pl.player_id WHERE slate_id=%s", (sid,))
        pool = cur.fetchall()
    rng = random.Random(3)
    teams = sorted({r["team"] for r in pool})
    team_rows, scores = {}, {}
    for t in teams:
        rows, mins_left = [], 240
        tp = [r for r in pool if r["team"] == t]
        for i, r in enumerate(tp):
            mn = 24 if i < 10 else 0
            pts = rng.randint(2, 30)
            rows.append((r["name"], mn, pts, rng.randint(0, 10), rng.randint(0, 8), 1, 0, 2, 1, mn == 0))
        total_min = sum(x[1] for x in rows)
        rows[0] = (rows[0][0], rows[0][1] + 240 - total_min, *rows[0][2:])
        team_rows[t] = rows
        scores[t] = sum(x[2] for x in rows if not x[9])
    # use the slate's real game pairs
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT game_list FROM slates WHERE id=%s", (sid,))
        pairs = [g["game"].split("@") for g in cur.fetchone()["game_list"]]
    day = {"games": [], "bref": {}, "boxes": {}}
    for i, (a, h) in enumerate(pairs):
        eid = str(i)
        day["games"].append({"event_id": eid, "final": True, "period": 4,
                             "teams": {a: {"score": scores[a]}, h: {"score": scores[h]}}})
        day["boxes"][eid] = parse_summary(_espn_summary({a: team_rows[a], h: team_rows[h]}))
        day["bref"][frozenset({a, h})] = {a: scores[a], h: scores[h]}
    with db.connect() as conn:
        rep = grading.grade_slate(conn, sid, day)
        assert rep["complete"], rep["games"]
        g = rep["grade"]
        assert g["mae_ours"] is not None and g["mae_rotogrinders"] is not None
        assert g["hindsight_optimal"]["salary"] <= 50000 and len(g["hindsight_optimal"]["players"]) == 8
        best_ours = g["lineups"]["best_generated"]["actual"]
        assert g["hindsight_optimal"]["score"] >= best_ours           # nothing beats hindsight
        assert g["lineups"]["scored"] >= 5
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lineups WHERE slate_id=%s AND actual_score IS NOT NULL", (sid,))
            assert cur.fetchone()["n"] == g["lineups"]["scored"]
        # one game fails verification -> slate not graded, actuals only for the verified game
    day2 = {**day, "bref": {k: v for i, (k, v) in enumerate(day["bref"].items()) if i == 0}}
    dk2 = dk.replace("12/10/2026", "12/11/2026")
    sid2 = pipeline.run_build(dk2, rg, n_gpp=2, n_cash=1, fetch=off)["slate_id"]
    with db.connect() as conn:
        rep2 = grading.grade_slate(conn, sid2, day2)
        assert not rep2["complete"] and "grade" not in rep2
        assert sum(1 for v in rep2["games"].values() if v == "verified") == 1


def test_parse_bref_day_real_markup():
    from pathlib import Path
    from dfs.sources.boxscores import parse_bref_day
    finals = parse_bref_day((Path(__file__).parent / "fixtures" / "bref_day_sample.html").read_text())
    assert finals[frozenset({"LAC", "GSW"})] == {"LAC": 112, "GSW": 124}
    assert finals[frozenset({"BKN", "IND"})] == {"BKN": 98, "IND": 95}          # BRK -> BKN
    assert frozenset({"PHX", "CHA"}) not in finals                              # in progress, not final
